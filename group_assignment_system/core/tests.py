import re
import tempfile
from datetime import timedelta
from pathlib import Path

import importlib
from unittest.mock import patch
from types import SimpleNamespace

from django.conf import settings
from django.apps import apps
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core import mail
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from .models import (
    AcademicSession,
    Assignment,
    AssignmentClassRepresentative,
    AuditLog,
    Course,
    CourseEnrolment,
    ContributionActivity,
    FileDocument,
    Group,
    GroupMembership,
    GroupMembershipEvent,
    GroupMessage,
    InternalDeadline,
    LecturerProfile,
    Notification,
    PeerReview,
    Semester,
    StudentProfile,
    Submission,
    Task,
    TaskAssignment,
    TaskUpdate,
    User,
)
from .forms import AssignmentForm, GroupDocumentUploadForm
from .services import (
    record_activity,
    appoint_assignment_class_representative,
    user_can_manage_assignment_groups,
    user_can_appoint_assignment_representative,
    user_can_access_group_content,
    user_is_assignment_representative,
    user_manages_assignment,
)


class GroupAssignmentWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.lecturer = User.objects.create_user(
            username='lecturer',
            password='test-password',
            role=User.ROLE_LECTURER,
        )
        cls.student = User.objects.create_user(
            username='student',
            password='test-password',
            role=User.ROLE_STUDENT,
        )
        cls.peer = User.objects.create_user(
            username='peer',
            password='test-password',
            role=User.ROLE_STUDENT,
        )
        cls.outsider = User.objects.create_user(
            username='outsider',
            password='test-password',
            role=User.ROLE_STUDENT,
        )
        cls.administrator = User.objects.create_user(
            username='administrator',
            password='test-password',
            role=User.ROLE_ADMIN,
        )
        cls.staff_student = User.objects.create_user(
            username='staff-student',
            password='test-password',
            role=User.ROLE_STUDENT,
            is_staff=True,
        )
        cls.other_lecturer = User.objects.create_user(
            username='other-lecturer',
            password='test-password',
            role=User.ROLE_LECTURER,
        )
        cls.unassigned_student = User.objects.create_user(
            username='unassigned',
            password='test-password',
            role=User.ROLE_STUDENT,
        )
        cls.invited_student = User.objects.create_user(
            username='invitee',
            password='test-password',
            role=User.ROLE_STUDENT,
        )
        cls.academic_session = AcademicSession.objects.create(
            name='2026',
            start_date='2026-01-01',
            end_date='2026-12-31',
        )
        cls.semester = Semester.objects.create(
            name='Fall',
            academic_session=cls.academic_session,
        )
        cls.course = Course.objects.create(
            lecturer=cls.lecturer,
            name='BIT326',
            code='BIT326',
            year_of_study=3,
            semester=2,
            legacy_semester='Fall',
            legacy_academic_year='2026',
            legacy_semester_record=cls.semester,
        )
        cls.assignment = Assignment.objects.create(
            course=cls.course,
            title='Group Project',
            description='System design',
            deadline=timezone.now() + timedelta(days=30),
            peer_evaluation_enabled=True,
        )
        for student in (
            cls.student,
            cls.peer,
            cls.outsider,
            cls.unassigned_student,
            cls.invited_student,
        ):
            CourseEnrolment.objects.create(course=cls.course, student=student)
        cls.group = Group.objects.create(assignment=cls.assignment, name='Team Alpha')
        GroupMembership.objects.create(group=cls.group, student=cls.student)
        GroupMembership.objects.create(group=cls.group, student=cls.peer)
        cls.private_group = Group.objects.create(
            assignment=cls.assignment,
            name='Private Team',
        )
        GroupMembership.objects.create(group=cls.private_group, student=cls.outsider)
        cls.other_course = Course.objects.create(
            lecturer=cls.other_lecturer,
            name='BIT999',
            code='BIT999',
            year_of_study=2,
            semester=1,
            legacy_semester='Fall',
            legacy_academic_year='2026',
            legacy_semester_record=cls.semester,
        )

    def login(self, user):
        self.client.force_login(user)

    def test_dashboard_requires_login_and_displays_group_for_member(self):
        response = self.client.get(reverse('dashboard'))
        self.assertRedirects(response, f'{reverse("login")}?next={reverse("dashboard")}')

        self.login(self.student)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Team Alpha')
        self.assertNotContains(response, 'Private Team')

    def test_dashboard_task_metrics_include_overdue_upcoming_and_student_assignments(self):
        Task.objects.create(
            group=self.group,
            title='Late task',
            description='Past due',
            assigned_to=self.student,
            deadline=timezone.now() - timedelta(days=1),
        )
        Task.objects.create(
            group=self.group,
            title='Upcoming task',
            description='Due soon',
            assigned_to=self.peer,
            deadline=timezone.now() + timedelta(days=2),
        )
        completed_task = Task.objects.create(
            group=self.group,
            title='Completed task',
            description='Already done',
            assigned_to=self.student,
            deadline=timezone.now() - timedelta(days=2),
            status=Task.STATUS_COMPLETED,
        )

        self.login(self.student)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['task_metrics'], {
            'total': 3,
            'completed': 1,
            'pending': 2,
            'overdue': 1,
            'assigned_to_me': 2,
        })
        self.assertContains(response, 'Upcoming internal deadlines')
        self.assertContains(response, 'Late task')
        self.assertNotIn(
            completed_task.pk,
            [task.pk for task in response.context['upcoming_tasks']],
        )
        self.login(self.lecturer)
        lecturer_response = self.client.get(reverse('dashboard'))
        self.assertEqual(lecturer_response.context['task_metrics']['total'], 3)
        self.assertIn(
            self.group.pk,
            [group.pk for group in lecturer_response.context['delayed_groups']],
        )

    def test_dashboard_and_navigation_are_specific_to_each_role(self):
        for user, heading in (
            (self.student, 'Student dashboard'),
            (self.lecturer, 'Lecturer dashboard'),
            (self.administrator, 'Administrator dashboard'),
        ):
            with self.subTest(role=user.role):
                self.login(user)
                response = self.client.get(reverse('dashboard'))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, heading)

        self.login(self.student)
        response = self.client.get(reverse('dashboard'))
        self.assertNotContains(response, reverse('admin:index'))
        self.assertNotContains(response, 'New course')

        self.login(self.administrator)
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, reverse('admin:index'))

    def test_lecturer_portal_connects_managed_work_to_participation_and_grading(self):
        private_assignment = Assignment.objects.create(
            course=self.other_course,
            title='Private lecturer assignment',
            description='Outside this lecturer scope',
            deadline=timezone.now() + timedelta(days=5),
        )
        Group.objects.create(
            assignment=private_assignment,
            name='Private lecturer group',
        )
        with tempfile.TemporaryDirectory() as media_dir:
            with override_settings(MEDIA_ROOT=Path(media_dir)):
                Submission.objects.create(
                    group=self.group,
                    submitted_by=self.student,
                    file=SimpleUploadedFile('lecturer-review.txt', b'submission evidence'),
                )
                self.login(self.lecturer)

                dashboard_response = self.client.get(reverse('dashboard'))
                self.assertEqual(dashboard_response.status_code, 200)
                self.assertContains(dashboard_response, 'Managed Courses')
                self.assertContains(dashboard_response, 'Group Task Monitoring')
                self.assertContains(dashboard_response, 'Review &amp; grade')
                self.assertContains(dashboard_response, 'Participation')
                self.assertNotContains(dashboard_response, 'Private lecturer assignment')

                assignments_response = self.client.get(reverse('assignment_list'))
                self.assertContains(assignments_response, 'Submissions')
                self.assertContains(assignments_response, 'Participation')
                self.assertNotContains(assignments_response, 'Private lecturer assignment')

                assignment_response = self.client.get(
                    reverse('assignment_detail', args=[self.assignment.pk])
                )
                self.assertContains(assignment_response, 'Review submissions')
                self.assertContains(assignment_response, 'Participation')
                self.assertContains(assignment_response, self.group.name)

                submissions_response = self.client.get(reverse('submission_list'))
                self.assertContains(submissions_response, 'Review / grade')
                self.assertNotContains(submissions_response, 'Private lecturer assignment')

                group_response = self.client.get(
                    reverse('group_detail', args=[self.group.pk])
                )
                self.assertContains(group_response, 'Assessment and lecturer feedback')
                self.assertContains(group_response, 'Save grade')
                self.assertContains(group_response, 'Review participation')

                participation_response = self.client.get(
                    reverse('reports'),
                    {'type': 'contribution', 'group': self.group.pk},
                )
                self.assertEqual(participation_response.status_code, 200)
                self.assertContains(participation_response, self.student.username)
                self.assertNotContains(participation_response, 'Private lecturer group')

    def test_portal_course_catalogue_and_navigation_are_role_aware(self):
        self.login(self.student)
        response = self.client.get(reverse('course_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'My Courses')
        self.assertContains(response, 'BIT326')
        self.assertContains(response, 'Open Course')
        self.assertContains(response, 'My Groups')
        self.assertContains(response, 'My Tasks')
        self.assertContains(response, 'Group Tasks')

        self.login(self.lecturer)
        response = self.client.get(reverse('course_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Courses')
        self.assertContains(response, 'BIT326')
        self.assertContains(response, 'My Courses')
        self.assertContains(response, 'Submissions')

        self.login(self.administrator)
        response = self.client.get(reverse('course_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Create Course')
        self.assertContains(response, 'Administration')

    def test_login_and_post_logout_protect_authenticated_pages(self):
        response = self.client.post(reverse('login'), {
            'username': self.student.username,
            'password': 'test-password',
        })
        self.assertRedirects(response, reverse('dashboard'))
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)

        self.assertEqual(self.client.get(reverse('logout')).status_code, 405)
        response = self.client.post(reverse('logout'))
        self.assertRedirects(response, reverse('login'))
        response = self.client.get(reverse('dashboard'))
        self.assertRedirects(
            response,
            f'{reverse("login")}?next={reverse("dashboard")}',
        )

    def test_invalid_login_is_rejected_and_does_not_create_authenticated_session(self):
        response = self.client.post(reverse('login'), {
            'username': self.student.username,
            'password': 'incorrect-password',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Username or password was not accepted.')
        response = self.client.get(reverse('dashboard'))
        self.assertRedirects(
            response,
            f'{reverse("login")}?next={reverse("dashboard")}',
        )

    def test_login_and_mutation_forms_enforce_csrf_tokens(self):
        csrf_client = self.client_class(enforce_csrf_checks=True)
        response = csrf_client.post(reverse('login'), {
            'username': self.student.username,
            'password': 'test-password',
        })
        self.assertEqual(response.status_code, 403)
        csrf_client.force_login(self.student)
        response = csrf_client.post(
            reverse('group_detail', args=[self.group.pk]),
            {'post_message': '1', 'body': 'Must include valid CSRF token.'},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(GroupMessage.objects.filter(
            body='Must include valid CSRF token.',
        ).exists())

    def test_group_discussion_content_is_html_escaped(self):
        GroupMessage.objects.create(
            group=self.group,
            author=self.student,
            body='<script>alert("xss")</script>',
        )
        self.login(self.student)
        response = self.client.get(reverse('group_detail', args=[self.group.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;')
        self.assertNotContains(response, '<script>alert("xss")</script>')

    def test_staff_flag_does_not_grant_academic_or_administrator_access(self):
        self.login(self.staff_student)
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'Student dashboard')
        self.assertNotContains(response, 'New course')
        response = self.client.post(reverse('course_create'), {
            'name': 'Forbidden course',
            'code': 'BAD100',
            'programme': 'BIT',
            'year_of_study': 1,
            'semester': 1,
        })
        self.assertEqual(response.status_code, 403)
        self.assertNotEqual(self.client.get(reverse('admin:index')).status_code, 200)

    def test_student_api_is_read_scoped_and_cannot_mutate(self):
        response = self.client.get(reverse('api-session'))
        self.assertIn(response.status_code, (401, 403))

        self.login(self.student)
        response = self.client.get(reverse('api-session'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], self.student.pk)
        self.assertEqual(response.data['role'], User.ROLE_STUDENT)

        response = self.client.get(reverse('api-groups-list'))
        self.assertEqual(response.status_code, 200)
        group_names = [group['name'] for group in response.data]
        self.assertEqual(group_names, ['Team Alpha'])
        self.assertEqual(response.data[0]['progress'], 0)
        self.assertEqual(response.data[0]['assignment_title'], self.assignment.title)
        self.assertEqual(response.data[0]['course_code'], self.course.code)
        self.assertEqual(
            {member['username'] for member in response.data[0]['members']},
            {'student', 'peer'},
        )
        self.assertNotIn('student_number', response.data[0]['members'][0])

        response = self.client.get(reverse('api-groups-detail', args=[self.private_group.pk]))
        self.assertEqual(response.status_code, 404)
        response = self.client.post(reverse('api-courses-list'), {
            'name': 'Unauthorized course',
            'code': 'BAD101',
        })
        self.assertEqual(response.status_code, 403)
        response = self.client.post(reverse('api-groups-list'), {
            'assignment': self.assignment.pk,
            'name': 'Unauthorized group',
        })
        self.assertEqual(response.status_code, 403)
        self.login(self.lecturer)
        lecturer_groups = self.client.get(reverse('api-groups-list'))
        self.assertEqual(lecturer_groups.status_code, 200)
        self.assertIn(
            'student_number',
            lecturer_groups.data[0]['members'][0],
        )
        self.login(self.student)
        task = Task.objects.create(
            group=self.group,
            title='Team planning',
            description='Prepare the team plan',
            assigned_to=self.student,
            deadline=timezone.now() + timedelta(days=1),
        )
        response = self.client.get(reverse('api-tasks-list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['id'] for item in response.data], [task.pk])
        self.assertEqual(response.data[0]['assignment'], self.assignment.pk)
        self.assertEqual(response.data[0]['assigned_to'], self.student.pk)
        self.assertEqual(response.data[0]['assigned_to_name'], self.student.username)
        self.assertEqual(response.data[0]['assignment_title'], self.assignment.title)
        self.assertEqual(response.data[0]['group_name'], self.group.name)
        self.assertEqual(response.data[0]['effective_status'], Task.STATUS_TODO)
        self.assertEqual(response.data[0]['group'], self.group.pk)
        self.assertIn('deadline', response.data[0])
        group_response = self.client.get(reverse('api-groups-detail', args=[self.group.pk]))
        self.assertEqual(group_response.data['progress'], 0)
        response = self.client.patch(
            reverse('api-tasks-detail', args=[task.pk]),
            {'status': Task.STATUS_COMPLETED},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        group_response = self.client.get(reverse('api-groups-detail', args=[self.group.pk]))
        self.assertEqual(group_response.data['progress'], 100)
        response = self.client.post(reverse('api-tasks-list'), {
            'group': self.group.pk,
            'title': 'Group member task',
            'description': 'Created by an authorised group member',
            'assigned_to': self.peer.pk,
            'deadline': (timezone.now() + timedelta(days=1)).isoformat(),
        })
        self.assertEqual(response.status_code, 201)
        created_task = Task.objects.get(title='Group member task')
        self.assertEqual(created_task.created_by, self.student)
        self.assertEqual(created_task.assigned_to, self.peer)
        self.assertTrue(
            created_task.activity_logs.filter(
                user=self.student,
                event_type='task_created',
            ).exists()
        )

        response = self.client.post(reverse('api-tasks-list'), {
            'group': self.private_group.pk,
            'title': 'Unrelated group task',
            'description': 'Must not be created',
            'assigned_to': self.outsider.pk,
            'deadline': (timezone.now() + timedelta(days=1)).isoformat(),
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Task.objects.filter(title='Unrelated group task').exists())

        response = self.client.post(reverse('api-tasks-list'), {
            'group': self.group.pk,
            'title': 'Outsider assignee task',
            'description': 'Must not be assigned outside the group',
            'assigned_to': self.outsider.pk,
            'deadline': (timezone.now() + timedelta(days=1)).isoformat(),
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Task.objects.filter(title='Outsider assignee task').exists())
        response = self.client.get(reverse('api-assignments-list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['id'] for item in response.data], [self.assignment.pk])

    def test_local_vite_origin_has_credentialed_cors_and_csrf_trust(self):
        frontend_origin = 'http://localhost:5173'
        self.assertIn(frontend_origin, settings.CORS_ALLOWED_ORIGINS)
        self.assertTrue(settings.CORS_ALLOW_CREDENTIALS)
        self.assertIn(frontend_origin, settings.CSRF_TRUSTED_ORIGINS)
        self.assertIn(
            'rest_framework.authentication.SessionAuthentication',
            settings.REST_FRAMEWORK['DEFAULT_AUTHENTICATION_CLASSES'],
        )

        response = self.client.options(
            reverse('api-session'),
            HTTP_ORIGIN=frontend_origin,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD='GET',
        )
        self.assertEqual(response['Access-Control-Allow-Origin'], frontend_origin)
        self.assertEqual(response['Access-Control-Allow-Credentials'], 'true')

        untrusted_response = self.client.options(
            reverse('api-session'),
            HTTP_ORIGIN='http://attacker.example',
            HTTP_ACCESS_CONTROL_REQUEST_METHOD='GET',
        )
        self.assertNotIn('Access-Control-Allow-Origin', untrusted_response)

    def test_authenticated_api_writes_require_csrf_and_trust_vite_origin(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.student)
        frontend_origin = 'http://localhost:5173'
        task_data = {
            'group': self.group.pk,
            'title': 'CSRF-protected task',
            'description': 'Created with the authenticated frontend session.',
            'assigned_to': self.student.pk,
            'deadline': (timezone.now() + timedelta(days=1)).isoformat(),
        }

        rejected = client.post(
            reverse('api-tasks-list'),
            task_data,
            content_type='application/json',
            HTTP_ORIGIN=frontend_origin,
        )
        self.assertEqual(rejected.status_code, 403)

        csrf_response = client.get(reverse('api-csrf'))
        self.assertEqual(csrf_response.status_code, 200)
        self.assertIn('csrftoken', client.cookies)
        csrf_token = csrf_response.json()['csrfToken']

        accepted = client.post(
            reverse('api-tasks-list'),
            task_data,
            content_type='application/json',
            HTTP_ORIGIN=frontend_origin,
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(accepted.status_code, 201)
        self.assertTrue(Task.objects.filter(title='CSRF-protected task').exists())

    def test_administrator_api_manages_users_roles_courses_and_activity(self):
        self.login(self.administrator)
        users_response = self.client.get(reverse('api-users-list'))
        self.assertEqual(users_response.status_code, 200)
        self.assertTrue(all('password' not in user for user in users_response.data))

        created_user_response = self.client.post(
            reverse('api-users-list'),
            {
                'username': 'api-managed-user',
                'first_name': 'API',
                'last_name': 'Managed',
                'email': 'api-managed@example.test',
                'role': User.ROLE_LECTURER,
                'password': 'X9!safe-Admin-Password-Value',
            },
            content_type='application/json',
        )
        self.assertEqual(created_user_response.status_code, 201)
        managed_user = User.objects.get(username='api-managed-user')
        self.assertTrue(managed_user.check_password('X9!safe-Admin-Password-Value'))
        self.assertNotIn('password', created_user_response.data)

        updated_user_response = self.client.patch(
            reverse('api-users-detail', args=[managed_user.pk]),
            {'role': User.ROLE_STUDENT, 'is_active': False},
            content_type='application/json',
        )
        self.assertEqual(updated_user_response.status_code, 200)
        managed_user.refresh_from_db()
        self.assertEqual(managed_user.role, User.ROLE_STUDENT)
        self.assertFalse(managed_user.is_active)

        course_response = self.client.post(
            reverse('api-courses-list'),
            {
                'code': 'ADM501',
                'name': 'Administration API Course',
                'programme': 'BIT',
                'year_of_study': 4,
                'semester': 1,
                'lecturer': self.lecturer.pk,
                'is_active': True,
            },
            content_type='application/json',
        )
        self.assertEqual(course_response.status_code, 201)
        course_id = course_response.data['id']
        course_update = self.client.patch(
            reverse('api-courses-detail', args=[course_id]),
            {'is_active': False},
            content_type='application/json',
        )
        self.assertEqual(course_update.status_code, 200)
        self.assertFalse(course_update.data['is_active'])

        record_activity(
            self.administrator,
            'admin_api_test',
            'Reviewed administrator API activity',
            assignment=self.assignment,
            group=self.group,
        )
        AuditLog.objects.create(
            user=self.administrator,
            action='Updated an administrator-managed record',
        )
        summary = self.client.get(reverse('api-admin-summary'))
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.data['users']['total'], User.objects.count())
        self.assertEqual(summary.data['courses']['total'], Course.objects.count())
        self.assertEqual(summary.data['assignments']['total'], Assignment.objects.count())
        self.assertEqual(summary.data['groups']['total'], Group.objects.count())

        activity = self.client.get(reverse('api-admin-activity'), {'limit': 5})
        self.assertEqual(activity.status_code, 200)
        self.assertLessEqual(len(activity.data), 5)
        self.assertTrue(any(
            entry['action'] == 'Reviewed administrator API activity'
            for entry in activity.data
        ))
        self.assertTrue(any(
            entry['action'] == 'Updated an administrator-managed record'
            for entry in activity.data
        ))

        self_response = self.client.patch(
            reverse('api-users-detail', args=[self.administrator.pk]),
            {'role': User.ROLE_STUDENT},
            content_type='application/json',
        )
        self.assertEqual(self_response.status_code, 400)
        self.assertTrue(User.objects.get(pk=self.administrator.pk).is_admin_role())

    def test_administrator_api_denies_students_and_invalid_activity_limits(self):
        self.login(self.student)
        self.assertEqual(self.client.get(reverse('api-users-list')).status_code, 403)
        self.assertEqual(self.client.get(reverse('api-admin-summary')).status_code, 403)
        self.assertEqual(self.client.get(reverse('api-admin-activity')).status_code, 403)
        self.assertEqual(self.client.post(reverse('api-courses-list'), {
            'name': 'Student-created course',
            'year_of_study': 1,
            'semester': 1,
        }).status_code, 403)

        self.login(self.administrator)
        invalid_limit = self.client.get(
            reverse('api-admin-activity'),
            {'limit': '101'},
        )
        self.assertEqual(invalid_limit.status_code, 400)

    def test_superuser_session_api_exposes_administrator_portal_role(self):
        superuser = User.objects.create_superuser(
            username='root-portal-user',
            email='root-portal@example.test',
            password='X9!safe-Root-Password-Value',
        )
        self.assertEqual(superuser.role, User.ROLE_STUDENT)
        self.login(superuser)

        session_response = self.client.get(reverse('api-session'))
        self.assertEqual(session_response.status_code, 200)
        self.assertEqual(session_response.data['role'], User.ROLE_ADMIN)
        self.assertEqual(self.client.get(reverse('api-users-list')).status_code, 200)

    def test_student_api_can_only_modify_tasks_in_their_group(self):
        task = Task.objects.create(
            group=self.group,
            title='Team planning',
            description='Prepare the team plan',
            assigned_to=self.student,
            deadline=timezone.now() + timedelta(days=2),
        )
        unrelated_task = Task.objects.create(
            group=self.private_group,
            title='Private planning',
            description='Keep this task private',
            assigned_to=self.outsider,
            deadline=timezone.now() + timedelta(days=2),
        )
        self.login(self.student)

        response = self.client.patch(
            reverse('api-tasks-detail', args=[task.pk]),
            {'status': Task.STATUS_IN_PROGRESS},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.status, Task.STATUS_IN_PROGRESS)
        self.assertTrue(
            task.activity_logs.filter(
                user=self.student,
                event_type='task_status',
            ).exists()
        )
        response = self.client.delete(reverse('api-tasks-detail', args=[task.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Task.objects.filter(pk=task.pk).exists())

        response = self.client.patch(
            reverse('api-tasks-detail', args=[unrelated_task.pk]),
            {'status': Task.STATUS_COMPLETED},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 404)
        unrelated_task.refresh_from_db()
        self.assertEqual(unrelated_task.status, Task.STATUS_TODO)

    def test_task_updates_record_authenticated_author_and_reject_spoofing(self):
        task = Task.objects.create(
            group=self.group,
            title='Research',
            description='Collect sources',
            assigned_to=self.student,
            deadline=timezone.now() + timedelta(days=2),
        )
        self.login(self.student)

        response = self.client.post(
            reverse('api-taskupdates-list'),
            {
                'task': task.pk,
                'author': self.outsider.pk,
                'update_text': 'Collected three sources.',
            },
        )

        self.assertEqual(response.status_code, 201)
        update = TaskUpdate.objects.get(task=task)
        self.assertEqual(update.author, self.student)
        self.assertEqual(response.data['author'], self.student.pk)
        self.assertEqual(response.data['author_name'], self.student.username)
        self.assertEqual(response.data['task_title'], task.title)
        self.assertTrue(
            task.activity_logs.filter(
                user=self.student,
                event_type='task_update',
            ).exists()
        )

        self.login(self.lecturer)
        response = self.client.patch(
            reverse('api-taskupdates-detail', args=[update.pk]),
            {'update_text': 'Reviewed and confirmed three sources.'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        update.refresh_from_db()
        self.assertEqual(update.author, self.student)
        self.assertTrue(
            task.activity_logs.filter(
                user=self.lecturer,
                event_type='task_update_edited',
            ).exists()
        )

        self.login(self.student)
        unrelated_task = Task.objects.create(
            group=self.private_group,
            title='Private research',
            description='Do not expose updates',
            assigned_to=self.outsider,
            deadline=timezone.now() + timedelta(days=2),
        )
        response = self.client.post(
            reverse('api-taskupdates-list'),
            {
                'task': unrelated_task.pk,
                'update_text': 'Should be rejected.',
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(TaskUpdate.objects.filter(task=unrelated_task).exists())

    def test_lecturer_and_administrator_task_api_access_is_scoped_and_audited(self):
        task = Task.objects.create(
            group=self.group,
            title='Lecturer-managed task',
            description='Review the report',
            assigned_to=self.student,
            deadline=timezone.now() + timedelta(days=2),
        )
        other_assignment = Assignment.objects.create(
            course=self.other_course,
            title='Other lecturer assignment',
            description='Another course',
            deadline=timezone.now() + timedelta(days=30),
        )
        other_group = Group.objects.create(
            assignment=other_assignment,
            name='Other lecturer group',
        )
        CourseEnrolment.objects.create(
            course=self.other_course,
            student=self.outsider,
        )
        GroupMembership.objects.create(group=other_group, student=self.outsider)

        self.login(self.lecturer)
        response = self.client.patch(
            reverse('api-tasks-detail', args=[task.pk]),
            {'status': Task.STATUS_IN_PROGRESS},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        task.refresh_from_db()
        self.assertTrue(
            task.activity_logs.filter(
                user=self.lecturer,
                event_type='task_status',
            ).exists()
        )

        response = self.client.post(reverse('api-tasks-list'), {
            'group': other_group.pk,
            'title': 'Cross-course task',
            'description': 'Should be rejected',
            'assigned_to': self.outsider.pk,
            'deadline': (timezone.now() + timedelta(days=1)).isoformat(),
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Task.objects.filter(title='Cross-course task').exists())
        foreign_task = Task.objects.create(
            group=other_group,
            title='Foreign task',
            description='Another lecturer task',
            assigned_to=self.outsider,
            deadline=timezone.now() + timedelta(days=1),
        )
        response = self.client.patch(
            reverse('api-tasks-detail', args=[foreign_task.pk]),
            {'status': Task.STATUS_IN_PROGRESS},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 404)
        foreign_task.refresh_from_db()
        self.assertEqual(foreign_task.status, Task.STATUS_TODO)

        self.login(self.administrator)
        response = self.client.post(reverse('api-tasks-list'), {
            'group': self.group.pk,
            'title': 'Administrator task',
            'description': 'Created by the administrator',
            'assigned_to': self.peer.pk,
            'deadline': (timezone.now() + timedelta(days=1)).isoformat(),
        })
        self.assertEqual(response.status_code, 201)
        administrator_task = Task.objects.get(title='Administrator task')
        self.assertEqual(administrator_task.created_by, self.administrator)
        response = self.client.patch(
            reverse('api-tasks-detail', args=[task.pk]),
            {'status': Task.STATUS_COMPLETED},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.status, Task.STATUS_COMPLETED)

    def test_task_api_exposes_read_only_ownership_and_audit_timestamps(self):
        task = Task.objects.create(
            group=self.group,
            title='Document research',
            description='Collect sources',
            created_by=self.lecturer,
            assigned_to=self.student,
            deadline=timezone.now() + timedelta(days=2),
        )

        self.login(self.lecturer)
        response = self.client.get(reverse('api-tasks-detail', args=[task.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['assignment'], self.assignment.pk)
        self.assertEqual(response.data['created_by'], self.lecturer.pk)
        self.assertEqual(parse_datetime(response.data['created_at']), task.created_at)
        self.assertEqual(parse_datetime(response.data['updated_at']), task.updated_at)

    def test_task_status_choices_are_canonical_and_overdue_is_derived(self):
        self.assertEqual(
            {value for value, _label in Task.STATUS_CHOICES},
            {
                Task.STATUS_TODO,
                Task.STATUS_IN_PROGRESS,
                Task.STATUS_UNDER_REVIEW,
                Task.STATUS_COMPLETED,
            },
        )
        self.assertNotIn(Task.STATUS_OVERDUE, {
            value for value, _label in Task.STATUS_CHOICES
        })
        self.assertFalse(hasattr(Task, 'STATUS_PENDING'))

    def test_progress_update_refreshes_task_last_updated_timestamp(self):
        task = Task.objects.create(
            group=self.group,
            title='Research',
            description='Collect sources',
            assigned_to=self.student,
            deadline=timezone.now() + timedelta(days=2),
        )
        previous_update = timezone.now() - timedelta(days=1)
        Task.objects.filter(pk=task.pk).update(updated_at=previous_update)

        TaskUpdate.objects.create(
            task=task,
            author=self.student,
            update_text='Collected three sources.',
        )

        task.refresh_from_db()
        self.assertGreater(task.updated_at, previous_update)

    def test_group_and_task_navigation_pages_are_role_scoped(self):
        Task.objects.create(
            group=self.group,
            title='Team planning',
            description='Prepare the team plan',
            assigned_to=self.student,
            deadline=timezone.now() + timedelta(days=1),
        )
        Task.objects.create(
            group=self.group,
            title='Peer research',
            description='Collect research sources',
            assigned_to=self.peer,
            deadline=timezone.now() + timedelta(days=2),
        )
        Task.objects.create(
            group=self.private_group,
            title='Private planning',
            description='Private group task',
            assigned_to=self.outsider,
            deadline=timezone.now() + timedelta(days=1),
        )

        self.login(self.student)
        response = self.client.get(reverse('group_list'), {'section': 'documents'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Group documents')
        self.assertContains(response, 'Team Alpha')
        self.assertNotContains(response, 'Private Team')
        response = self.client.get(reverse('task_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'My Tasks')
        self.assertContains(response, 'Team planning')
        self.assertNotContains(response, 'Peer research')
        self.assertContains(response, 'Assigned to me')
        self.assertNotContains(response, 'Private planning')
        self.assertEqual(response.context['my_task_count'], 1)
        self.assertEqual(response.context['group_task_count'], 2)
        response = self.client.get(reverse('task_list'), {'scope': 'group'})
        self.assertContains(response, 'Group Tasks')
        self.assertContains(response, 'Team planning')
        self.assertContains(response, 'Peer research')
        self.assertContains(response, 'Group member')
        self.assertNotContains(response, 'Private planning')
        self.assertEqual(response.context['my_task_count'], 1)
        self.assertEqual(response.context['group_task_count'], 2)
        group_page = self.client.get(reverse('group_detail', args=[self.group.pk]))
        self.assertContains(group_page, 'Create &amp; assign task')
        self.assertContains(group_page, 'Assigned to me')
        self.assertContains(group_page, reverse('task_create', args=[self.group.pk]))
        dashboard = self.client.get(reverse('dashboard'))
        self.assertContains(dashboard, 'My Groups')
        self.assertContains(dashboard, 'Course Overview')
        self.assertContains(dashboard, 'Contributions')

        self.login(self.lecturer)
        response = self.client.get(reverse('group_list'))
        self.assertContains(response, 'Team Alpha')
        self.assertContains(response, 'Private Team')
        self.assertContains(response, 'Progress')

        self.login(self.other_lecturer)
        response = self.client.get(reverse('group_list'))
        self.assertNotContains(response, 'Team Alpha')

        self.login(self.administrator)
        response = self.client.get(reverse('group_list'))
        self.assertContains(response, 'Team Alpha')
        self.assertContains(response, 'Administration')

    def test_lecturer_api_rejects_assignments_for_unmanaged_courses(self):
        self.login(self.lecturer)
        response = self.client.post(reverse('api-assignments-list'), {
            'course': self.other_course.pk,
            'title': 'Cross-course assignment',
            'description': 'Should not be created',
            'deadline': (timezone.now() + timedelta(days=4)).isoformat(),
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Assignment.objects.filter(title='Cross-course assignment').exists())

    def test_lecturer_cannot_edit_assignment_for_another_lecturers_course(self):
        self.login(self.other_lecturer)
        response = self.client.get(reverse('assignment_edit', args=[self.assignment.pk]))
        self.assertEqual(response.status_code, 403)
        response = self.client.post(reverse('assignment_edit', args=[self.assignment.pk]), {
            'title': 'Unauthorized edit',
        })
        self.assertEqual(response.status_code, 403)
        self.assignment.refresh_from_db()
        self.assertNotEqual(self.assignment.title, 'Unauthorized edit')

    def test_administrator_role_can_manage_django_admin_without_superuser_status(self):
        self.assertFalse(self.administrator.is_superuser)
        self.login(self.administrator)
        response = self.client.get(reverse('admin:index'))
        self.assertEqual(response.status_code, 200)
        response = self.client.get(reverse('admin:core_user_changelist'))
        self.assertEqual(response.status_code, 200)
        response = self.client.get(reverse('admin:core_user_add'))
        self.assertEqual(response.status_code, 200)
        response = self.client.post(reverse('admin:core_user_add'), {
            'username': 'new-student',
            'email': 'new-student@example.edu',
            'password1': 'Another-Unique-Password-348!',
            'password2': 'Another-Unique-Password-348!',
            'role': User.ROLE_STUDENT,
            'is_staff': 'on',
            'is_superuser': 'on',
        })
        self.assertEqual(response.status_code, 302)
        new_user = User.objects.get(username='new-student')
        self.assertEqual(new_user.role, User.ROLE_STUDENT)
        self.assertFalse(new_user.is_staff)
        self.assertFalse(new_user.is_superuser)
        self.assertTrue(new_user.check_password('Another-Unique-Password-348!'))

        self.client.logout()
        response = self.client.post(reverse('admin:login'), {
            'username': self.administrator.username,
            'password': 'test-password',
            'next': reverse('admin:index'),
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('admin:index'))

        self.client.logout()
        response = self.client.post(reverse('admin:login'), {
            'username': self.staff_student.username,
            'password': 'test-password',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.wsgi_request.user.is_authenticated)
    def test_administrator_can_create_course_and_assign_a_lecturer(self):
        self.login(self.administrator)
        course_form = self.client.get(reverse('course_create'))
        self.assertEqual(course_form.status_code, 200)
        self.assertContains(course_form, 'Year of study')
        self.assertContains(course_form, 'Year 1')
        self.assertContains(course_form, 'Year 4')
        self.assertContains(course_form, 'Semester 1')
        self.assertContains(course_form, 'Semester 2')
        self.assertNotContains(course_form, 'Academic Calendar')
        self.assertNotContains(course_form, 'Course start date')
        self.assertNotContains(course_form, 'Course end date')
        response = self.client.post(reverse('course_create'), {
            'name': 'BIT327',
            'code': 'BIT327',
            'programme': 'BIT',
            'year_of_study': 3,
            'semester': 2,
            'lecturer': self.lecturer.pk,
            'is_active': 'on',
        })
        course = Course.objects.get(name='BIT327')
        self.assertRedirects(response, reverse('course_detail', args=[course.pk]))
        self.assertEqual(course.lecturer, self.lecturer)
        self.assertTrue(AuditLog.objects.filter(user=self.administrator).exists())
        self.assertEqual(
            self.client.get(reverse('admin:core_course_add')).status_code,
            200,
        )

    def test_courses_can_be_created_with_each_valid_classification(self):
        self.login(self.administrator)
        endpoint = reverse('api-courses-list')
        for code, year, semester in (
            ('TEST101', 1, 1),
            ('TEST202', 2, 2),
            ('BIT326A', 3, 2),
            ('TEST401', 4, 1),
        ):
            with self.subTest(code=code):
                response = self.client.post(
                    endpoint,
                    {
                        'code': code,
                        'name': f'Course {code}',
                        'programme': 'BIT',
                        'year_of_study': year,
                        'semester': semester,
                    },
                    content_type='application/json',
                )
                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.data['year_of_study'], year)
                self.assertEqual(response.data['semester'], semester)

    def test_course_classification_is_required_and_choice_limited_in_api(self):
        self.login(self.administrator)
        endpoint = reverse('api-courses-list')
        base_payload = {'code': 'VALIDATION101', 'name': 'Validation course'}
        for payload, field in (
            ({}, 'year_of_study'),
            ({'year_of_study': 1}, 'semester'),
            ({'year_of_study': 5, 'semester': 1}, 'year_of_study'),
            ({'year_of_study': 1, 'semester': 3}, 'semester'),
        ):
            response = self.client.post(
                endpoint,
                {**base_payload, **payload},
                content_type='application/json',
            )
            self.assertEqual(response.status_code, 400)
            self.assertIn(field, response.data)
        self.assertFalse(Course.objects.filter(code='VALIDATION101').exists())

    def test_courses_can_be_filtered_by_year_semester_name_and_code(self):
        self.login(self.administrator)
        endpoint = reverse('api-courses-list')
        expected = {
            'year_of_study': {self.course.pk},
            'semester': {self.course.pk},
            'name': {self.course.pk},
            'code': {self.other_course.pk},
        }
        params = {
            'year_of_study': {'year_of_study': 3},
            'semester': {'semester': 2},
            'name': {'name': 'BIT326'},
            'code': {'code': 'BIT999'},
        }
        for filter_name, query in params.items():
            with self.subTest(filter=filter_name):
                response = self.client.get(endpoint, query)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    {item['id'] for item in response.data},
                    expected[filter_name],
                )

    def test_administrator_can_update_classification_and_legacy_data_is_preserved(self):
        self.login(self.administrator)
        response = self.client.patch(
            reverse('api-courses-detail', args=[self.course.pk]),
            {'year_of_study': 4, 'semester': 1},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.course.refresh_from_db()
        self.assertEqual((self.course.year_of_study, self.course.semester), (4, 1))
        self.assertEqual(self.course.legacy_academic_year, '2026')
        self.assertEqual(self.course.legacy_semester, 'Fall')
        self.assertEqual(self.course.legacy_semester_record, self.semester)
        self.assertTrue(Assignment.objects.filter(course=self.course).exists())
        self.assertTrue(CourseEnrolment.objects.filter(course=self.course).exists())

    def test_classification_form_rejects_missing_values_and_calendar_module_is_removed(self):
        self.login(self.administrator)
        response = self.client.post(reverse('course_create'), {
            'name': 'Missing classification',
            'code': 'NOCLASS',
            'programme': 'BIT',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Course.objects.filter(code='NOCLASS').exists())
        self.assertEqual(self.client.get('/academic-calendar/').status_code, 404)

    def test_existing_unclassified_course_can_be_safely_assigned(self):
        Course.objects.bulk_create([Course(
            code='OLD100',
            name='Legacy unclassified course',
            legacy_academic_year='2026',
            legacy_semester='Semester 1',
        )])
        legacy = Course.objects.get(code='OLD100')
        self.assertEqual(legacy.legacy_academic_year, '2026')
        self.assertEqual(legacy.legacy_semester, 'Semester 1')
        self.assertIsNone(legacy.year_of_study)
        self.assertIsNone(legacy.semester)
        self.login(self.administrator)
        response = self.client.patch(
            reverse('api-courses-detail', args=[legacy.pk]),
            {'year_of_study': 1, 'semester': 1},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        legacy.refresh_from_db()
        self.assertEqual(legacy.get_year_of_study_display(), 'Year 1')

    def test_lecturer_creates_publishes_edits_and_closes_assignment(self):
        self.login(self.lecturer)
        published = timezone.now() - timedelta(hours=1)
        deadline = timezone.now() + timedelta(days=14)
        response = self.client.post(reverse('assignment_create'), {
            'course': self.course.pk,
            'title': 'Research Project',
            'description': 'Detailed project instructions.',
            'objectives': 'Demonstrate research and collaboration.',
            'moodle_reference': 'https://moodle.example.edu/course/12/assignments/7',
            'published_at': timezone.localtime(published).strftime('%Y-%m-%dT%H:%M'),
            'deadline': timezone.localtime(deadline).strftime('%Y-%m-%dT%H:%M'),
            'status': Assignment.STATUS_PUBLISHED,
            'min_group_size': 2,
            'max_group_size': 5,
            'allow_student_group_proposals': 'on',
            'allow_late_submissions': 'on',
        })
        assignment = Assignment.objects.get(title='Research Project')
        self.assertRedirects(response, reverse('assignment_detail', args=[assignment.pk]))
        self.assertEqual(assignment.objectives, 'Demonstrate research and collaboration.')
        self.assertTrue(assignment.is_published())
        self.assertEqual(
            assignment.moodle_reference,
            'https://moodle.example.edu/course/12/assignments/7',
        )
        self.assertTrue(Notification.objects.filter(
            user=self.student,
            message__contains='Research Project',
            assignment=assignment,
            notification_type=Notification.TYPE_ASSIGNMENT,
        ).exists())

        response = self.client.post(reverse('assignment_edit', args=[assignment.pk]), {
            'course': self.course.pk,
            'title': 'Updated Research Project',
            'description': 'Updated instructions.',
            'objectives': 'Updated objectives.',
            'published_at': timezone.localtime(published).strftime('%Y-%m-%dT%H:%M'),
            'deadline': timezone.localtime(deadline).strftime('%Y-%m-%dT%H:%M'),
            'status': Assignment.STATUS_CLOSED,
            'min_group_size': 2,
            'max_group_size': 5,
        })
        self.assertRedirects(response, reverse('assignment_detail', args=[assignment.pk]))
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, Assignment.STATUS_CLOSED)
        self.assertEqual(assignment.description, 'Updated instructions.')

    def test_students_only_view_published_course_assignments_and_cannot_edit(self):
        draft = Assignment.objects.create(
            course=self.course,
            title='Private Draft',
            description='Not for students.',
            deadline=timezone.now() + timedelta(days=10),
            status=Assignment.STATUS_DRAFT,
        )
        future = Assignment.objects.create(
            course=self.course,
            title='Future Publication',
            description='Not public yet.',
            objectives='Future objectives.',
            deadline=timezone.now() + timedelta(days=20),
            published_at=timezone.now() + timedelta(days=2),
            status=Assignment.STATUS_PUBLISHED,
        )
        self.login(self.student)
        response = self.client.get(reverse('course_detail', args=[self.course.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.assignment.title)
        self.assertNotContains(response, draft.title)
        self.assertNotContains(response, future.title)

        response = self.client.get(reverse('assignment_list'))
        self.assertContains(response, self.assignment.title)
        self.assertNotContains(response, draft.title)
        self.assertNotContains(response, future.title)
        self.assertEqual(
            self.client.get(reverse('assignment_detail', args=[draft.pk])).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(reverse('assignment_detail', args=[future.pk])).status_code,
            403,
        )

        response = self.client.get(reverse('assignment_detail', args=[self.assignment.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.assignment.description)
        self.assertContains(response, self.group.name)
        self.assertNotContains(response, self.private_group.name)

        response = self.client.post(reverse('assignment_edit', args=[self.assignment.pk]), {
            'title': 'Student changed title',
        })
        self.assertEqual(response.status_code, 403)
        self.assignment.refresh_from_db()
        self.assertNotEqual(self.assignment.title, 'Student changed title')

        response = self.client.get(reverse('course_detail', args=[self.other_course.pk]))
        self.assertEqual(response.status_code, 403)

    def test_lecturer_assignment_creation_is_limited_to_assigned_active_courses(self):
        self.login(self.lecturer)
        response = self.client.post(reverse('assignment_create'), {
            'course': self.other_course.pk,
            'title': 'Unauthorized course work',
            'description': 'Instructions',
            'objectives': 'Objectives',
            'published_at': timezone.localtime(timezone.now()).strftime('%Y-%m-%dT%H:%M'),
            'deadline': timezone.localtime(timezone.now() + timedelta(days=5)).strftime(
                '%Y-%m-%dT%H:%M'
            ),
            'status': Assignment.STATUS_PUBLISHED,
            'min_group_size': 2,
            'max_group_size': 5,
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Assignment.objects.filter(title='Unauthorized course work').exists())

    def test_assignment_forms_reject_invalid_publication_deadlines_and_group_sizes(self):
        self.login(self.lecturer)
        response = self.client.post(reverse('assignment_create'), {
            'course': self.course.pk,
            'title': 'Invalid Assignment',
            'description': 'Instructions',
            'objectives': 'Objectives',
            'published_at': timezone.localtime(timezone.now() + timedelta(days=5)).strftime(
                '%Y-%m-%dT%H:%M'
            ),
            'deadline': timezone.localtime(timezone.now() + timedelta(days=2)).strftime(
                '%Y-%m-%dT%H:%M'
            ),
            'status': Assignment.STATUS_PUBLISHED,
            'min_group_size': 6,
            'max_group_size': 2,
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Assignment.objects.filter(title='Invalid Assignment').exists())

    def test_lecturers_cannot_manage_courses(self):
        self.login(self.lecturer)
        response = self.client.get(reverse('course_create'))
        self.assertEqual(response.status_code, 403)

    def test_students_cannot_manage_courses(self):
        self.login(self.student)
        response = self.client.get(reverse('course_create'))
        self.assertEqual(response.status_code, 403)

    def test_lecturer_can_add_student_to_group(self):
        self.login(self.lecturer)
        response = self.client.post(reverse('group_detail', args=[self.group.pk]), {
            'student': self.unassigned_student.pk,
            'add_member': '1',
        })
        self.assertRedirects(response, reverse('group_detail', args=[self.group.pk]))
        self.assertTrue(
            GroupMembership.objects.filter(
                group=self.group,
                student=self.unassigned_student,
                status=GroupMembership.STATUS_APPROVED,
            ).exists()
        )
        self.assertEqual(
            set(
                GroupMembershipEvent.objects.filter(
                    group=self.group,
                    student=self.unassigned_student,
                ).values_list('action', flat=True)
            ),
            {
                GroupMembershipEvent.ACTION_ADDED,
                GroupMembershipEvent.ACTION_APPROVED,
            },
        )
        self.assertTrue(Notification.objects.filter(user=self.unassigned_student).exists())

    def test_student_proposes_group_invites_classmate_and_lecturer_approves(self):
        self.login(self.unassigned_student)
        response = self.client.get(reverse('assignment_detail', args=[self.assignment.pk]))
        self.assertContains(response, 'Propose a group')
        response = self.client.post(
            reverse('assignment_detail', args=[self.assignment.pk]),
            {
                'name': 'Student Research Team',
                'description': 'A student-proposed collaboration group.',
                'invitees': [self.invited_student.pk],
                'propose_group': '1',
            },
        )
        group = Group.objects.get(name='Student Research Team')
        self.assertRedirects(response, reverse('group_detail', args=[group.pk]))
        self.assertEqual(group.status, Group.STATUS_PENDING)
        self.assertEqual(group.formation_type, Group.FORMATION_STUDENT)
        self.assertEqual(group.leader, self.unassigned_student)
        self.assertEqual(group.member_count(), 0)
        self.assertTrue(
            GroupMembership.objects.filter(
                group=group,
                student=self.unassigned_student,
                status=GroupMembership.STATUS_PENDING,
            ).exists()
        )
        self.assertTrue(
            GroupMembership.objects.filter(
                group=group,
                student=self.invited_student,
                status=GroupMembership.STATUS_INVITED,
            ).exists()
        )
        self.assertEqual(
            set(
                GroupMembershipEvent.objects.filter(group=group).values_list(
                    'action',
                    flat=True,
                )
            ),
            {
                GroupMembershipEvent.ACTION_ADDED,
                GroupMembershipEvent.ACTION_INVITED,
            },
        )

        self.login(self.invited_student)
        response = self.client.post(reverse('group_detail', args=[group.pk]), {
            'accept_invitation': '1',
        })
        self.assertRedirects(response, reverse('assignment_detail', args=[self.assignment.pk]))
        invitation = GroupMembership.objects.get(group=group, student=self.invited_student)
        self.assertEqual(invitation.status, GroupMembership.STATUS_PENDING)

        self.login(self.lecturer)
        response = self.client.post(reverse('group_detail', args=[group.pk]), {
            'approve_group': '1',
        })
        self.assertRedirects(response, reverse('group_detail', args=[group.pk]))
        group.refresh_from_db()
        self.assertEqual(group.status, Group.STATUS_ACTIVE)
        self.assertEqual(group.member_count(), 2)
        self.assertEqual(group.leader, self.unassigned_student)
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, GroupMembership.STATUS_APPROVED)
        self.assertEqual(
            GroupMembershipEvent.objects.filter(
                group=group,
                action=GroupMembershipEvent.ACTION_APPROVED,
            ).count(),
            2,
        )

        self.login(self.invited_student)
        response = self.client.get(reverse('group_detail', args=[group.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Student Research Team')
        self.assertContains(response, 'Group leader')

    def test_student_requests_to_join_group_and_lecturer_approves_request(self):
        self.group.max_members = 3
        self.group.save()
        self.login(self.unassigned_student)
        response = self.client.post(
            reverse('assignment_detail', args=[self.assignment.pk]),
            {'group_id': self.group.pk, 'request_join': '1'},
        )
        self.assertRedirects(response, reverse('assignment_detail', args=[self.assignment.pk]))
        membership = GroupMembership.objects.get(
            assignment=self.assignment,
            student=self.unassigned_student,
        )
        self.assertEqual(membership.group, self.group)
        self.assertEqual(membership.status, GroupMembership.STATUS_PENDING)
        self.assertEqual(
            list(
                GroupMembershipEvent.objects.filter(
                    group=self.group,
                    student=self.unassigned_student,
                ).values_list('action', flat=True)
            ),
            [GroupMembershipEvent.ACTION_ADDED],
        )
        self.assertEqual(
            self.client.get(reverse('group_detail', args=[self.group.pk])).status_code,
            403,
        )

        self.login(self.lecturer)
        response = self.client.post(reverse('group_detail', args=[self.group.pk]), {
            'membership_id': membership.pk,
            'approve_member': '1',
        })
        self.assertRedirects(response, reverse('group_detail', args=[self.group.pk]))
        membership.refresh_from_db()
        self.group.refresh_from_db()
        self.assertEqual(membership.status, GroupMembership.STATUS_APPROVED)
        self.assertEqual(self.group.leader, self.unassigned_student)
        self.assertEqual(self.group.member_count(), 3)
        self.assertEqual(
            set(
                GroupMembershipEvent.objects.filter(
                    group=self.group,
                    student=self.unassigned_student,
                ).values_list('action', flat=True)
            ),
            {
                GroupMembershipEvent.ACTION_ADDED,
                GroupMembershipEvent.ACTION_APPROVED,
            },
        )

    def test_student_can_reapply_to_group_after_request_is_rejected(self):
        membership = GroupMembership.objects.create(
            group=self.group,
            student=self.unassigned_student,
            status=GroupMembership.STATUS_REJECTED,
        )
        self.login(self.unassigned_student)
        response = self.client.get(reverse('assignment_detail', args=[self.assignment.pk]))
        self.assertContains(response, 'Request to join')
        response = self.client.post(
            reverse('assignment_detail', args=[self.assignment.pk]),
            {'group_id': self.group.pk, 'request_join': '1'},
        )
        self.assertRedirects(response, reverse('assignment_detail', args=[self.assignment.pk]))
        membership.refresh_from_db()
        self.assertEqual(membership.group, self.group)
        self.assertEqual(membership.status, GroupMembership.STATUS_PENDING)

    def test_group_capacity_and_assignment_membership_are_enforced(self):
        self.group.max_members = 2
        self.group.save()
        self.login(self.unassigned_student)
        response = self.client.post(
            reverse('assignment_detail', args=[self.assignment.pk]),
            {'group_id': self.group.pk, 'request_join': '1'},
            follow=True,
        )
        self.assertRedirects(response, reverse('assignment_detail', args=[self.assignment.pk]))
        self.assertContains(response, 'maximum capacity')
        self.assertFalse(
            GroupMembership.objects.filter(
                assignment=self.assignment,
                student=self.unassigned_student,
            ).exists()
        )

        with self.assertRaises(ValidationError):
            GroupMembership.objects.create(
                group=self.private_group,
                student=self.student,
            )

    def test_lecturer_creates_assignment_group_and_sets_group_capacity(self):
        self.login(self.lecturer)
        response = self.client.post(
            reverse('assignment_group_create', args=[self.assignment.pk]),
            {
                'assignment': self.assignment.pk,
                'name': 'Lecturer Allocation',
                'description': 'Assigned by lecturer.',
                'max_members': 4,
            },
        )
        group = Group.objects.get(name='Lecturer Allocation')
        self.assertRedirects(response, reverse('group_detail', args=[group.pk]))
        self.assertEqual(group.assignment, self.assignment)
        self.assertEqual(group.capacity, 4)
        self.assertEqual(group.formation_type, Group.FORMATION_LECTURER)
        self.assertEqual(group.status, Group.STATUS_ACTIVE)

    def test_group_creation_allocates_eligible_students_and_records_audit_events(self):
        self.unassigned_student.student_profile.student_number = 'UNILUS-NEW-01'
        self.unassigned_student.student_profile.programme = 'BIT'
        self.unassigned_student.student_profile.save()
        self.invited_student.student_profile.programme = 'BIT'
        self.invited_student.student_profile.save()
        self.login(self.lecturer)
        url = reverse('assignment_group_create', args=[self.assignment.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Student ID Not Provided')
        self.assertContains(response, 'UNILUS-NEW-01')
        self.assertContains(response, 'Registered')

        response = self.client.post(url, {
            'assignment': self.assignment.pk,
            'name': 'Initial Allocation',
            'description': 'Allocated on creation.',
            'max_members': 4,
            'students': [self.unassigned_student.pk, self.invited_student.pk],
        })
        group = Group.objects.get(name='Initial Allocation')
        self.assertRedirects(response, reverse('group_detail', args=[group.pk]))
        self.assertEqual(
            set(group.memberships.values_list('student_id', flat=True)),
            {self.unassigned_student.pk, self.invited_student.pk},
        )
        self.assertEqual(group.member_count(), 2)
        self.assertIsNone(group.leader)
        self.assertEqual(
            set(group.memberships.values_list('status', flat=True)),
            {GroupMembership.STATUS_APPROVED},
        )
        self.assertEqual(
            set(
                GroupMembershipEvent.objects.filter(group=group).values_list(
                    'action',
                    flat=True,
                )
            ),
            {
                GroupMembershipEvent.ACTION_ADDED,
                GroupMembershipEvent.ACTION_APPROVED,
            },
        )
        self.assertEqual(GroupMembershipEvent.objects.filter(group=group).count(), 4)

    def test_administrator_can_create_groups_for_draft_assignments(self):
        draft = Assignment.objects.create(
            course=self.course,
            title='Draft group work',
            description='Not published yet.',
            deadline=timezone.now() + timedelta(days=14),
            status=Assignment.STATUS_DRAFT,
        )
        self.login(self.administrator)
        response = self.client.get(reverse('group_create'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Draft group work')
        response = self.client.post(reverse('group_create'), {
            'assignment': draft.pk,
            'name': 'Draft Allocation',
            'description': 'Groups before publication.',
            'max_members': 4,
            'students': [self.unassigned_student.pk],
        })
        group = Group.objects.get(name='Draft Allocation')
        self.assertRedirects(response, reverse('group_detail', args=[group.pk]))
        self.assertEqual(group.assignment, draft)
        self.assertTrue(
            GroupMembership.objects.filter(
                group=group,
                student=self.unassigned_student,
                status=GroupMembership.STATUS_APPROVED,
            ).exists()
        )

    def test_lecturer_can_create_groups_for_draft_and_scheduled_assignments(self):
        draft = Assignment.objects.create(
            course=self.course,
            title='Lecturer draft',
            description='Not published.',
            deadline=timezone.now() + timedelta(days=14),
            status=Assignment.STATUS_DRAFT,
        )
        scheduled = Assignment.objects.create(
            course=self.course,
            title='Lecturer scheduled',
            description='Future publication.',
            deadline=timezone.now() + timedelta(days=20),
            status=Assignment.STATUS_PUBLISHED,
            published_at=timezone.now() + timedelta(days=3),
        )
        self.login(self.lecturer)
        response = self.client.get(reverse('group_create'))
        self.assertContains(response, 'Lecturer draft')
        self.assertContains(response, 'Lecturer scheduled')
        for assignment, name in (
            (draft, 'Lecturer Draft Group'),
            (scheduled, 'Lecturer Scheduled Group'),
        ):
            response = self.client.post(reverse('group_create'), {
                'assignment': assignment.pk,
                'name': name,
                'description': 'Created before publication.',
                'max_members': 4,
                'students': [],
            })
            group = Group.objects.get(name=name)
            self.assertRedirects(response, reverse('group_detail', args=[group.pk]))
            self.assertEqual(group.assignment, assignment)
            self.assertEqual(group.created_by, self.lecturer)

    def test_representative_can_create_only_for_appointed_enrolled_assignment(self):
        draft = Assignment.objects.create(
            course=self.course,
            title='Representative draft',
            description='SECRET_DRAFT_INSTRUCTIONS_57291',
            deadline=timezone.now() + timedelta(days=14),
            status=Assignment.STATUS_DRAFT,
        )
        appoint_assignment_class_representative(
            self.lecturer,
            draft,
            self.unassigned_student,
        )
        self.login(self.unassigned_student)
        dashboard_response = self.client.get(reverse('dashboard'))
        self.assertContains(dashboard_response, 'Representative draft')
        self.assertContains(dashboard_response, 'Manage groups')
        detail_response = self.client.get(
            reverse('assignment_detail', args=[draft.pk])
        )
        self.assertEqual(detail_response.status_code, 200)
        self.assertNotContains(detail_response, 'SECRET_DRAFT_INSTRUCTIONS_57291')
        self.assertEqual(
            self.client.get(reverse('assignment_edit', args=[draft.pk])).status_code,
            403,
        )
        url = reverse('assignment_group_create', args=[draft.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        response = self.client.post(url, {
            'assignment': draft.pk,
            'name': 'Representative Allocation',
            'description': 'Created by the appointed representative.',
            'max_members': 4,
            'students': [self.invited_student.pk],
        })
        group = Group.objects.get(name='Representative Allocation')
        self.assertRedirects(
            response,
            reverse('assignment_detail', args=[draft.pk]),
        )
        self.assertEqual(group.created_by, self.unassigned_student)
        self.assertTrue(
            GroupMembership.objects.filter(
                group=group,
                student=self.invited_student,
                status=GroupMembership.STATUS_APPROVED,
            ).exists()
        )

        other_assignment = Assignment.objects.create(
            course=self.course,
            title='Not appointed',
            description='Out of representative scope.',
            deadline=timezone.now() + timedelta(days=14),
            status=Assignment.STATUS_DRAFT,
        )
        self.assertEqual(
            self.client.get(
                reverse('assignment_group_create', args=[other_assignment.pk])
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                reverse('assignment_group_students', args=[other_assignment.pk])
            ).status_code,
            403,
        )
        response = self.client.post(reverse('api-groups-list'), {
            'assignment': other_assignment.pk,
            'name': 'Out of scope API group',
            'max_members': 4,
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Group.objects.filter(name='Out of scope API group').exists())
        CourseEnrolment.objects.filter(
            course=self.course,
            student=self.unassigned_student,
        ).delete()
        self.assertEqual(
            self.client.get(url).status_code,
            403,
        )

    def test_group_creation_rejects_non_enrolled_students_and_students_already_allocated(self):
        CourseEnrolment.objects.create(
            course=self.other_course,
            student=self.outsider,
        )
        self.login(self.lecturer)
        url = reverse('assignment_group_create', args=[self.assignment.pk])
        response = self.client.get(
            reverse('assignment_group_students', args=[self.assignment.pk])
        )
        self.assertEqual(response.status_code, 200)
        eligible_ids = {row['id'] for row in response.json()['students']}
        self.assertNotIn(self.outsider.pk, eligible_ids)
        self.assertNotIn(self.student.pk, eligible_ids)
        self.assertNotIn(self.peer.pk, eligible_ids)

        response = self.client.post(url, {
            'assignment': self.assignment.pk,
            'name': 'Ineligible Allocation',
            'description': '',
            'max_members': 4,
            'students': [self.outsider.pk],
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Group.objects.filter(name='Ineligible Allocation').exists())
        response = self.client.post(reverse('api-groups-list'), {
            'assignment': self.assignment.pk,
            'name': 'API Ineligible Allocation',
            'max_members': 4,
            'initial_students': [self.outsider.pk],
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Group.objects.filter(name='API Ineligible Allocation').exists())

        response = self.client.post(url, {
            'assignment': self.assignment.pk,
            'name': 'Duplicate Allocation',
            'description': '',
            'max_members': 4,
            'students': [self.student.pk],
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Group.objects.filter(name='Duplicate Allocation').exists())

    def test_group_creation_enforces_capacity_and_rolls_back_on_audit_failure(self):
        extra_student = User.objects.create_user(
            username='capacity-student',
            password='test-password',
            role=User.ROLE_STUDENT,
        )
        CourseEnrolment.objects.create(course=self.course, student=extra_student)
        url = reverse('assignment_group_create', args=[self.assignment.pk])
        self.login(self.lecturer)
        response = self.client.post(url, {
            'assignment': self.assignment.pk,
            'name': 'Over Capacity',
            'description': '',
            'max_members': 2,
            'students': [
                self.unassigned_student.pk,
                self.invited_student.pk,
                extra_student.pk,
            ],
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Group.objects.filter(name='Over Capacity').exists())

        with patch.object(
            GroupMembershipEvent,
            'save',
            side_effect=ValidationError('Audit event failure.'),
        ):
            response = self.client.post(url, {
                'assignment': self.assignment.pk,
                'name': 'Rolled Back Allocation',
                'description': '',
                'max_members': 4,
                'students': [self.unassigned_student.pk],
            })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Group.objects.filter(name='Rolled Back Allocation').exists())
        self.assertFalse(
            GroupMembership.objects.filter(
                assignment=self.assignment,
                student=self.unassigned_student,
            ).exists()
        )

    def test_representative_api_group_creation_is_scoped_and_student_api_creation_is_denied(self):
        draft = Assignment.objects.create(
            course=self.course,
            title='API representative assignment',
            description='Group assignment.',
            deadline=timezone.now() + timedelta(days=14),
            status=Assignment.STATUS_DRAFT,
        )
        appoint_assignment_class_representative(
            self.lecturer,
            draft,
            self.unassigned_student,
        )
        self.login(self.unassigned_student)
        response = self.client.post(reverse('api-groups-list'), {
            'assignment': draft.pk,
            'name': 'API Representative Group',
            'description': 'Created through the scoped API.',
            'max_members': 4,
            'initial_students': [self.invited_student.pk],
        })
        self.assertEqual(response.status_code, 201)
        group = Group.objects.get(name='API Representative Group')
        self.assertTrue(
            GroupMembership.objects.filter(
                group=group,
                student=self.invited_student,
                status=GroupMembership.STATUS_APPROVED,
            ).exists()
        )

        self.login(self.student)
        self.assertEqual(self.client.get(reverse('group_create')).status_code, 403)
        self.assertEqual(self.client.get(
            reverse('assignment_group_create', args=[draft.pk])
        ).status_code, 403)
        response = self.client.post(reverse('group_create'), {
            'assignment': draft.pk,
            'name': 'Unauthorized web group',
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Group.objects.filter(name='Unauthorized web group').exists())
        response = self.client.post(reverse('api-groups-list'), {
            'assignment': draft.pk,
            'name': 'Unauthorized API Group',
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Group.objects.filter(name='Unauthorized API Group').exists())

    def test_assignment_type_defaults_to_group_and_representative_is_assignment_scoped(self):
        self.assertEqual(self.assignment.assignment_type, Assignment.TYPE_GROUP)
        self.assignment.assignment_type = Assignment.TYPE_INDIVIDUAL
        with self.assertRaises(ValidationError):
            self.assignment.save()
        self.assignment.refresh_from_db()

        appointment = AssignmentClassRepresentative.objects.create(
            assignment=self.assignment,
            student=self.student,
            appointed_by=self.lecturer,
        )

        self.assertTrue(user_is_assignment_representative(self.student, self.assignment))
        self.assertTrue(user_can_manage_assignment_groups(self.student, self.assignment))
        self.assertFalse(user_manages_assignment(self.student, self.assignment))
        self.assertFalse(user_can_access_group_content(self.student, self.private_group))
        self.assertFalse(user_can_manage_assignment_groups(self.unassigned_student, self.assignment))

        other_assignment = Assignment.objects.create(
            course=self.course,
            title='Different group assignment',
            description='Another assignment in the same course.',
            deadline=timezone.now() + timedelta(days=10),
        )
        self.assertFalse(user_is_assignment_representative(self.student, other_assignment))
        self.assertFalse(user_can_manage_assignment_groups(self.student, other_assignment))

        appointment.status = AssignmentClassRepresentative.STATUS_REMOVED
        appointment.ended_at = timezone.now()
        appointment.ended_by = self.lecturer
        appointment.save()
        self.assertFalse(user_can_manage_assignment_groups(self.student, self.assignment))
        self.assertTrue(user_can_manage_assignment_groups(self.lecturer, self.assignment))
        self.assertTrue(user_can_manage_assignment_groups(self.administrator, self.assignment))

    def test_representative_authorization_checks_current_course_enrolment(self):
        appointment = AssignmentClassRepresentative.objects.create(
            assignment=self.assignment,
            student=self.student,
            appointed_by=self.lecturer,
        )
        self.assertTrue(user_can_manage_assignment_groups(self.student, self.assignment))
        CourseEnrolment.objects.filter(
            course=self.course,
            student=self.student,
        ).delete()
        self.assertFalse(user_is_assignment_representative(self.student, self.assignment))
        self.assertFalse(user_can_manage_assignment_groups(self.student, self.assignment))
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, AssignmentClassRepresentative.STATUS_ACTIVE)
        appointment.status = AssignmentClassRepresentative.STATUS_REMOVED
        appointment.ended_at = timezone.now()
        appointment.ended_by = self.lecturer
        appointment.save()
        self.assertEqual(appointment.status, AssignmentClassRepresentative.STATUS_REMOVED)

    def test_appointing_officer_must_manage_assignment(self):
        self.assertFalse(
            user_can_appoint_assignment_representative(
                self.other_lecturer,
                self.assignment,
            )
        )
        with self.assertRaises(ValidationError):
            AssignmentClassRepresentative.objects.create(
                assignment=self.assignment,
                student=self.student,
                appointed_by=self.other_lecturer,
            )
        with self.assertRaises(ValidationError):
            appoint_assignment_class_representative(
                self.other_lecturer,
                self.assignment,
                self.student,
            )

        appointment = appoint_assignment_class_representative(
            self.lecturer,
            self.assignment,
            self.student,
        )
        self.assertEqual(appointment.student, self.student)
        self.assertEqual(appointment.appointed_by, self.lecturer)

    def test_representative_appointment_validation_handles_missing_references(self):
        appointment = AssignmentClassRepresentative(
            student=self.student,
            appointed_by=self.lecturer,
        )
        with self.assertRaisesMessage(ValidationError, 'Select an assignment'):
            appointment.full_clean()

        appointment.assignment = self.assignment
        appointment.student = None
        with self.assertRaisesMessage(ValidationError, 'active appointment requires a student'):
            appointment.full_clean()

    def test_representative_appointment_requires_enrolled_active_student_and_unique_active_role(self):
        unenrolled = User.objects.create_user(
            username='unenrolled-representative',
            password='test-password',
            role=User.ROLE_STUDENT,
        )
        with self.assertRaises(ValidationError):
            AssignmentClassRepresentative.objects.create(
                assignment=self.assignment,
                student=unenrolled,
                appointed_by=self.lecturer,
            )

        appointment = AssignmentClassRepresentative.objects.create(
            assignment=self.assignment,
            student=self.student,
            appointed_by=self.lecturer,
        )
        with self.assertRaises(ValidationError):
            AssignmentClassRepresentative.objects.create(
                assignment=self.assignment,
                student=self.peer,
                appointed_by=self.lecturer,
            )

        appointment.status = AssignmentClassRepresentative.STATUS_REPLACED
        appointment.ended_at = timezone.now()
        appointment.ended_by = self.lecturer
        appointment.save()
        replacement = AssignmentClassRepresentative.objects.create(
            assignment=self.assignment,
            student=self.peer,
            appointed_by=self.administrator,
        )
        self.assertEqual(replacement.status, AssignmentClassRepresentative.STATUS_ACTIVE)

    def test_deleting_revoked_or_active_representative_preserves_appointment_history(self):
        revoked = AssignmentClassRepresentative.objects.create(
            assignment=self.assignment,
            student=self.student,
            appointed_by=self.lecturer,
        )
        revoked.status = AssignmentClassRepresentative.STATUS_REMOVED
        revoked.ended_at = timezone.now()
        revoked.ended_by = self.lecturer
        revoked.save()
        revoked_snapshot = (
            revoked.student_username_snapshot,
            revoked.student_full_name_snapshot,
            revoked.student_number_snapshot,
        )
        self.student.delete()
        revoked.refresh_from_db()
        self.assertIsNone(revoked.student_id)
        self.assertEqual(revoked.status, AssignmentClassRepresentative.STATUS_REMOVED)
        self.assertEqual(
            (
                revoked.student_username_snapshot,
                revoked.student_full_name_snapshot,
                revoked.student_number_snapshot,
            ),
            revoked_snapshot,
        )

        active_student = User.objects.create_user(
            username='active-representative',
            password='test-password',
            role=User.ROLE_STUDENT,
        )
        CourseEnrolment.objects.create(course=self.course, student=active_student)
        active = AssignmentClassRepresentative.objects.create(
            assignment=self.assignment,
            student=active_student,
            appointed_by=self.lecturer,
        )
        active_snapshot = active.student_username_snapshot
        active_student.delete()
        active.refresh_from_db()
        self.assertEqual(active.status, AssignmentClassRepresentative.STATUS_REMOVED)
        self.assertIsNone(active.student_id)
        self.assertEqual(active.student_username_snapshot, active_snapshot)

    def test_individual_submission_requires_enrolment_and_group_submission_stays_compatible(self):
        submission = Submission.objects.create(
            group=self.group,
            submitted_by=self.student,
            file=SimpleUploadedFile('group-work.txt', b'group work'),
        )
        self.assertEqual(submission.assignment, self.assignment)
        self.assertEqual(submission.group, self.group)

        individual_assignment = Assignment.objects.create(
            course=self.course,
            assignment_type=Assignment.TYPE_INDIVIDUAL,
            title='Individual work',
            description='Submit independently.',
            deadline=timezone.now() + timedelta(days=10),
        )
        individual_submission = Submission.objects.create(
            assignment=individual_assignment,
            submitted_by=self.student,
            file=SimpleUploadedFile('individual-work.txt', b'individual work'),
        )
        self.assertIsNone(individual_submission.group)
        self.assertEqual(individual_submission.assignment, individual_assignment)

        unenrolled = User.objects.create_user(
            username='unenrolled-submitter',
            password='test-password',
            role=User.ROLE_STUDENT,
        )
        with self.assertRaises(ValidationError):
            Submission.objects.create(
                assignment=individual_assignment,
                submitted_by=unenrolled,
                file=SimpleUploadedFile('not-enrolled.txt', b'not enrolled'),
            )
        with self.assertRaises(ValidationError):
            Submission.objects.create(
                assignment=self.assignment,
                submitted_by=self.student,
                file=SimpleUploadedFile('missing-group.txt', b'missing group'),
            )

    def test_group_membership_events_are_validated_and_immutable(self):
        unenrolled = User.objects.create_user(
            username='unenrolled-event-student',
            password='test-password',
            role=User.ROLE_STUDENT,
        )
        event = GroupMembershipEvent.objects.create(
            assignment=self.assignment,
            group=self.group,
            student=self.student,
            actor=self.lecturer,
            action=GroupMembershipEvent.ACTION_ADDED,
        )
        event.action = GroupMembershipEvent.ACTION_REMOVED
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            event.delete()
        with self.assertRaises(ValidationError):
            GroupMembershipEvent.objects.filter(pk=event.pk).update(
                action=GroupMembershipEvent.ACTION_REMOVED,
            )
        with self.assertRaises(ValidationError):
            GroupMembershipEvent.objects.filter(pk=event.pk).delete()
        with self.assertRaises(ValidationError):
            GroupMembershipEvent.objects.bulk_create([event])

        event_admin = admin.site._registry[GroupMembershipEvent]
        request = SimpleNamespace(user=self.administrator)
        self.assertFalse(event_admin.has_add_permission(request))
        self.assertFalse(event_admin.has_change_permission(request, event))
        self.assertFalse(event_admin.has_delete_permission(request, event))

        with self.assertRaises(ValidationError):
            GroupMembershipEvent.objects.create(
                assignment=self.assignment,
                group=self.group,
                student=unenrolled,
                actor=self.lecturer,
                action=GroupMembershipEvent.ACTION_ADDED,
            )

    def test_membership_event_preserves_identity_and_group_transfer_history(self):
        destination = Group.objects.create(
            assignment=self.assignment,
            name='Transfer Destination',
        )
        event = GroupMembershipEvent.objects.create(
            assignment=self.assignment,
            group=destination,
            previous_group=self.group,
            student=self.student,
            actor=self.student,
            action=GroupMembershipEvent.ACTION_TRANSFERRED,
        )
        self.assertEqual(event.group_name_snapshot, destination.name)
        self.assertEqual(event.previous_group_name_snapshot, self.group.name)
        self.assertEqual(event.student_username_snapshot, self.student.username)
        self.assertEqual(event.actor_username_snapshot, self.student.username)

        student_id = self.student.pk
        self.student.delete()
        event.refresh_from_db()
        self.assertIsNone(event.student_id)
        self.assertIsNone(event.actor_id)
        self.assertEqual(event.student_username_snapshot, 'student')
        self.assertEqual(event.actor_username_snapshot, 'student')
        self.assertEqual(event.group_name_snapshot, 'Transfer Destination')
        self.assertEqual(event.previous_group_name_snapshot, 'Team Alpha')
        self.assertNotEqual(student_id, event.student_id)

    def test_individual_submission_migration_rollback_guard(self):
        migration = importlib.import_module(
            'core.migrations.0022_phase2_integrity'
        )
        schema_editor = SimpleNamespace(connection=connection)
        migration.refuse_reverse_with_individual_submissions(apps, schema_editor)

        individual_assignment = Assignment.objects.create(
            course=self.course,
            assignment_type=Assignment.TYPE_INDIVIDUAL,
            title='Rollback guard test',
            description='Individual submissions prevent a lossy downgrade.',
            deadline=timezone.now() + timedelta(days=10),
        )
        Submission.objects.create(
            assignment=individual_assignment,
            submitted_by=self.peer,
            file=SimpleUploadedFile('rollback-test.txt', b'preserve this'),
        )
        with self.assertRaisesMessage(
            RuntimeError,
            'Cannot reverse Phase 2 while individual submissions exist',
        ):
            migration.refuse_reverse_with_individual_submissions(apps, schema_editor)

    def test_student_group_proposals_are_rejected_when_assignment_disallows_them(self):
        self.assignment.allow_student_group_proposals = False
        self.assignment.save()
        self.login(self.unassigned_student)
        response = self.client.post(
            reverse('assignment_detail', args=[self.assignment.pk]),
            {
                'name': 'Disallowed Proposal',
                'description': 'Should not be created.',
                'propose_group': '1',
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Group.objects.filter(name='Disallowed Proposal').exists())

    def test_lecturer_can_reject_proposal_and_student_can_propose_again(self):
        self.login(self.unassigned_student)
        response = self.client.post(
            reverse('assignment_detail', args=[self.assignment.pk]),
            {'name': 'First Proposal', 'description': 'Awaiting review.', 'propose_group': '1'},
        )
        self.assertEqual(response.status_code, 302)
        group = Group.objects.get(name='First Proposal')

        self.login(self.lecturer)
        response = self.client.post(reverse('group_detail', args=[group.pk]), {
            'reject_group': '1',
        })
        self.assertRedirects(response, reverse('assignment_detail', args=[self.assignment.pk]))
        group.refresh_from_db()
        self.assertEqual(group.status, Group.STATUS_REJECTED)
        self.assertIsNone(group.leader)
        membership = GroupMembership.objects.get(group=group, student=self.unassigned_student)
        self.assertEqual(membership.status, GroupMembership.STATUS_REJECTED)
        self.assertTrue(Notification.objects.filter(user=self.unassigned_student).exists())

        self.login(self.unassigned_student)
        response = self.client.post(
            reverse('assignment_detail', args=[self.assignment.pk]),
            {'name': 'Second Proposal', 'description': 'Updated proposal.', 'propose_group': '1'},
        )
        self.assertEqual(response.status_code, 302)
        membership.refresh_from_db()
        self.assertEqual(membership.group.name, 'Second Proposal')
        self.assertEqual(membership.status, GroupMembership.STATUS_PENDING)

    def test_lecturer_can_remove_member_and_transfer_group_leadership(self):
        self.group.leader = self.student
        self.group.save()
        membership = GroupMembership.objects.get(group=self.group, student=self.student)
        self.login(self.lecturer)
        response = self.client.post(reverse('group_detail', args=[self.group.pk]), {
            'membership_id': membership.pk,
            'remove_member': '1',
        })
        self.assertRedirects(response, reverse('group_detail', args=[self.group.pk]))
        self.assertFalse(
            GroupMembership.objects.filter(group=self.group, student=self.student).exists()
        )
        self.group.refresh_from_db()
        self.assertEqual(self.group.leader, self.peer)

    def test_nonmember_cannot_view_group(self):
        self.login(self.outsider)
        response = self.client.get(reverse('group_detail', args=[self.group.pk]))
        self.assertEqual(response.status_code, 403)

    def test_invited_student_cannot_view_group_private_content(self):
        self.group.max_members = 5
        self.group.save()
        GroupMembership.objects.create(
            group=self.group,
            student=self.invited_student,
            status=GroupMembership.STATUS_INVITED,
        )
        task = Task.objects.create(
            group=self.group,
            description='Confidential task',
            assigned_to=self.student,
            deadline=timezone.now() + timedelta(days=2),
        )

        with tempfile.TemporaryDirectory() as media_dir:
            with override_settings(MEDIA_ROOT=Path(media_dir)):
                evidence = FileDocument.objects.create(
                    task=task,
                    group=self.group,
                    uploaded_by=self.student,
                    file=SimpleUploadedFile('evidence.txt', b'private evidence'),
                    original_filename='evidence.txt',
                    document_type=FileDocument.TYPE_CONTRIBUTION_EVIDENCE,
                )
                submission = Submission.objects.create(
                    group=self.group,
                    submitted_by=self.student,
                    file=SimpleUploadedFile('submission.txt', b'private submission'),
                    original_filename='submission.txt',
                )
                self.login(self.invited_student)
                response = self.client.get(reverse('group_detail', args=[self.group.pk]))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'Group space is private')
                self.assertNotContains(response, 'Approved members')
                self.assertNotContains(response, 'Confidential task')
                self.assertNotContains(response, self.student.username)
                self.assertEqual(
                    self.client.get(reverse('task_detail', args=[task.pk])).status_code,
                    403,
                )
                self.assertEqual(
                    self.client.get(reverse('evidence_download', args=[evidence.pk])).status_code,
                    403,
                )
                self.assertEqual(
                    self.client.get(
                        reverse('submission_receipt', args=[submission.pk])
                    ).status_code,
                    403,
                )
                self.assertEqual(
                    self.client.get(
                        reverse('submission_download', args=[submission.pk])
                    ).status_code,
                    403,
                )
                self.assertEqual(
                    self.client.post(
                        reverse('group_detail', args=[self.group.pk]),
                        {
                            'upload_document': '1',
                            'document': SimpleUploadedFile(
                                'private.txt',
                                b'not authorized',
                                content_type='text/plain',
                            ),
                        },
                    ).status_code,
                    403,
                )

    def test_group_workspace_uploads_versions_and_protects_document_lifecycle(self):
        supported_types = (
            ('brief.pdf', 'application/pdf'),
            ('notes.doc', 'application/msword'),
            (
                'report.docx',
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            ),
            ('data.xls', 'application/vnd.ms-excel'),
            (
                'data.xlsx',
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            ),
            ('slides.ppt', 'application/vnd.ms-powerpoint'),
            (
                'slides.pptx',
                'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            ),
            ('readme.txt', 'text/plain'),
            ('archive.zip', 'application/zip'),
        )
        with tempfile.TemporaryDirectory() as media_dir:
            with override_settings(MEDIA_ROOT=Path(media_dir)):
                self.login(self.student)
                response = self.client.get(reverse('group_detail', args=[self.group.pk]))
                self.assertEqual(response.status_code, 200)
                for filename, content_type in supported_types:
                    response = self.client.post(
                        reverse('group_detail', args=[self.group.pk]),
                        {
                            'upload_document': '1',
                            'document': SimpleUploadedFile(
                                filename,
                                b'academic document',
                                content_type=content_type,
                            ),
                        },
                    )
                    self.assertRedirects(
                        response,
                        reverse('group_detail', args=[self.group.pk]),
                    )
                self.assertEqual(
                    FileDocument.objects.filter(
                        group=self.group,
                        document_type=FileDocument.TYPE_GROUP_FILE,
                    ).count(),
                    len(supported_types),
                )
                self.assertTrue(Notification.objects.filter(
                    user=self.peer,
                    group=self.group,
                    notification_type=Notification.TYPE_DOCUMENT,
                ).exists())

                filename = 'project_notes.txt'
                for content in (b'first version', b'second version'):
                    response = self.client.post(
                        reverse('group_detail', args=[self.group.pk]),
                        {
                            'upload_document': '1',
                            'document': SimpleUploadedFile(
                                filename,
                                content,
                                content_type='text/plain',
                            ),
                        },
                    )
                    self.assertRedirects(
                        response,
                        reverse('group_detail', args=[self.group.pk]),
                    )
                versions = list(
                    FileDocument.objects.filter(
                        group=self.group,
                        original_filename=filename,
                    ).order_by('version')
                )
                self.assertEqual([document.version for document in versions], [1, 2])
                self.assertTrue(
                    self.group.activity_logs.filter(
                        event_type='group_document_uploaded',
                    ).count() >= len(supported_types) + 2
                )

                response = self.client.get(reverse('group_detail', args=[self.group.pk]))
                self.assertContains(response, 'Shared documents')
                self.assertContains(response, 'Activity history')
                self.assertContains(response, 'Uploaded by')
                self.assertContains(response, 'v1')
                self.assertContains(response, 'v2')

                latest = versions[-1]
                response = self.client.get(
                    reverse('group_document_download', args=[latest.pk])
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response['Content-Type'], 'application/octet-stream')
                self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
                self.assertEqual(b''.join(response.streaming_content), b'second version')
                response.close()
                self.assertEqual(self.client.get(latest.file.url).status_code, 404)

                self.login(self.peer)
                response = self.client.post(
                    reverse('group_document_delete', args=[latest.pk])
                )
                self.assertEqual(response.status_code, 403)

                self.login(self.other_lecturer)
                self.assertEqual(
                    self.client.get(
                        reverse('group_document_download', args=[latest.pk])
                    ).status_code,
                    403,
                )

                self.login(self.student)
                response = self.client.post(
                    reverse('group_document_delete', args=[versions[0].pk])
                )
                self.assertRedirects(
                    response,
                    reverse('group_detail', args=[self.group.pk]),
                )
                self.assertFalse(FileDocument.objects.filter(pk=versions[0].pk).exists())

                self.login(self.lecturer)
                response = self.client.get(
                    reverse('group_document_download', args=[latest.pk])
                )
                self.assertEqual(response.status_code, 200)
                response.close()
                response = self.client.post(
                    reverse('group_document_delete', args=[latest.pk])
                )
                self.assertRedirects(
                    response,
                    reverse('group_detail', args=[self.group.pk]),
                )
                self.assertFalse(FileDocument.objects.filter(pk=latest.pk).exists())
                self.assertTrue(
                    self.group.activity_logs.filter(
                        event_type='group_document_deleted',
                    ).exists()
                )

                self.login(self.outsider)
                protected_document = FileDocument.objects.get(
                    group=self.group,
                    original_filename='brief.pdf',
                )
                self.assertEqual(
                    self.client.get(
                        reverse('group_document_download', args=[protected_document.pk])
                    ).status_code,
                    403,
                )

    def test_group_document_upload_rejects_unsupported_and_oversized_files(self):
        unsupported = SimpleUploadedFile(
            'program.exe',
            b'not a document',
            content_type='application/octet-stream',
        )
        form = GroupDocumentUploadForm(files={'document': unsupported})
        self.assertFalse(form.is_valid())
        self.assertIn('document', form.errors)

        oversized = SimpleUploadedFile(
            'large.pdf',
            b'x' * (GroupDocumentUploadForm.MAX_FILE_SIZE + 1),
            content_type='application/pdf',
        )
        form = GroupDocumentUploadForm(files={'document': oversized})
        self.assertFalse(form.is_valid())
        self.assertIn('25 MB or smaller', str(form.errors['document']))

    def test_group_discussions_are_private_and_added_to_activity_history(self):
        task = Task.objects.create(
            group=self.group,
            title='Research outline',
            description='Prepare the research outline.',
            assigned_to=self.student,
            deadline=timezone.now() + timedelta(days=3),
        )
        self.login(self.student)
        response = self.client.post(
            reverse('group_detail', args=[self.group.pk]),
            {
                'post_message': '1',
                'task': task.pk,
                'body': 'Let us review the research outline.',
            },
        )
        self.assertRedirects(response, reverse('group_detail', args=[self.group.pk]))
        message = self.group.messages.get(body='Let us review the research outline.')
        self.assertEqual(message.task, task)
        self.assertTrue(
            self.group.activity_logs.filter(event_type='group_message', task=task).exists()
        )
        message_notification = Notification.objects.get(
            user=self.peer,
            group=self.group,
            notification_type=Notification.TYPE_MESSAGE,
        )
        self.assertEqual(message_notification.get_target_url(), reverse(
            'group_detail',
            args=[self.group.pk],
        ))

        self.login(self.lecturer)
        response = self.client.get(reverse('group_detail', args=[self.group.pk]))
        self.assertContains(response, 'Let us review the research outline.')
        self.assertContains(response, 'Research outline')
        self.assertContains(response, 'Discussions')
        self.assertContains(response, 'Activity History')

        self.login(self.outsider)
        self.assertEqual(
            self.client.get(reverse('group_detail', args=[self.group.pk])).status_code,
            403,
        )

    def test_group_member_can_create_task_assigned_to_group_member(self):
        self.login(self.student)
        form_response = self.client.get(reverse('task_create', args=[self.group.pk]))
        self.assertEqual(form_response.status_code, 200)
        self.assertContains(form_response, 'Assign to group member')
        self.assertContains(form_response, 'Choose the student responsible for completing this task.')
        deadline = timezone.localtime(timezone.now() + timedelta(days=3)).replace(
            second=0,
            microsecond=0,
        )
        response = self.client.post(reverse('task_create', args=[self.group.pk]), {
            'title': 'Design',
            'description': 'Prepare the system design',
            'assigned_to': self.peer.pk,
            'deadline': deadline.strftime('%Y-%m-%dT%H:%M'),
        })
        task = Task.objects.get(description='Prepare the system design')
        self.assertRedirects(response, reverse('task_detail', args=[task.pk]))
        self.assertEqual(task.created_by, self.student)
        self.assertEqual(task.assigned_to, self.peer)
        self.assertEqual(task.deadline, deadline)
        self.assertTrue(TaskAssignment.objects.filter(task=task, student=self.peer).exists())
        self.assertEqual(task.internal_deadline.due_at, task.deadline)
        self.assertTrue(Notification.objects.filter(user=self.peer).exists())
        task_notification = Notification.objects.filter(
            user=self.peer,
            task=task,
            notification_type=Notification.TYPE_TASK,
        ).get()
        self.assertEqual(task_notification.get_target_url(), reverse('task_detail', args=[task.pk]))
        self.assertEqual(task.priority, Task.PRIORITY_NORMAL)
        self.assertTrue(task.activity_logs.filter(event_type='task_created').exists())

    def test_approved_group_member_can_update_task_status_and_completion_is_recorded(self):
        task = Task.objects.create(
            group=self.group,
            description='Complete report',
            assigned_to=self.peer,
            deadline=timezone.now() + timedelta(days=2),
        )
        self.login(self.student)
        detail_response = self.client.get(reverse('task_detail', args=[task.pk]))
        self.assertContains(detail_response, 'assigned to peer')
        self.assertContains(detail_response, 'Update progress')
        response = self.client.post(reverse('task_detail', args=[task.pk]), {
            'add_update': '1',
            'update_text': 'The report draft is ready for review.',
        })
        self.assertRedirects(response, reverse('task_detail', args=[task.pk]))
        update = task.updates.get(update_text='The report draft is ready for review.')
        self.assertEqual(update.author, self.student)
        self.assertTrue(
            task.activity_logs.filter(
                user=self.student,
                event_type='task_update',
            ).exists()
        )
        response = self.client.post(reverse('task_detail', args=[task.pk]), {
            'change_status': '1',
            'status': Task.STATUS_COMPLETED,
        })
        self.assertRedirects(response, reverse('task_detail', args=[task.pk]))
        task.refresh_from_db()
        self.assertEqual(task.status, Task.STATUS_COMPLETED)
        self.assertIsNotNone(task.completed_at)
        self.assertTrue(task.activity_logs.filter(event_type='task_status').exists())

        self.login(self.outsider)
        response = self.client.post(reverse('task_detail', args=[task.pk]), {
            'change_status': '1',
            'status': Task.STATUS_IN_PROGRESS,
        })
        self.assertEqual(response.status_code, 403)
        task.refresh_from_db()
        self.assertEqual(task.status, Task.STATUS_COMPLETED)

    def test_group_member_can_edit_task_description_assignment_deadline_and_priority(self):
        task = Task.objects.create(
            group=self.group,
            title='Initial task',
            description='Initial description',
            assigned_to=self.peer,
            deadline=timezone.now() + timedelta(days=2),
        )
        original_updated_at = task.updated_at
        new_deadline = timezone.localtime(timezone.now() + timedelta(days=5)).replace(
            second=0,
            microsecond=0,
        )
        self.login(self.student)
        response = self.client.get(reverse('task_detail', args=[task.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context['task_form']['deadline'].value().strftime('%Y-%m-%dT%H:%M'),
            timezone.localtime(task.deadline).strftime('%Y-%m-%dT%H:%M'),
        )
        response = self.client.post(reverse('task_detail', args=[task.pk]), {
            'update_task': '1',
            'title': 'Updated task',
            'description': 'Revised task instructions',
            'assigned_to': self.student.pk,
            'deadline': new_deadline.strftime('%Y-%m-%dT%H:%M'),
            'priority': Task.PRIORITY_URGENT,
        })
        self.assertRedirects(response, reverse('task_detail', args=[task.pk]))
        task.refresh_from_db()
        self.assertEqual(task.title, 'Updated task')
        self.assertEqual(task.description, 'Revised task instructions')
        self.assertEqual(task.assigned_to, self.student)
        self.assertEqual(task.priority, Task.PRIORITY_URGENT)
        self.assertEqual(task.deadline, new_deadline)
        self.assertGreater(task.updated_at, original_updated_at)
        self.assertTrue(task.activity_logs.filter(event_type='task_updated').exists())
        self.assertTrue(TaskAssignment.objects.filter(
            task=task,
            student=self.student,
        ).exists())

    def test_overdue_status_is_derived_and_invalid_status_is_rejected(self):
        task = Task.objects.create(
            group=self.group,
            description='Past-due task',
            assigned_to=self.student,
            deadline=timezone.now() - timedelta(hours=1),
        )
        self.assertTrue(task.is_overdue())
        self.assertEqual(task.effective_status, Task.STATUS_OVERDUE)
        self.assertEqual(task.effective_status_display, 'Overdue')

        self.login(self.student)
        response = self.client.post(reverse('task_detail', args=[task.pk]), {
            'change_status': '1',
            'status': Task.STATUS_OVERDUE,
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Select a valid choice')
        task.refresh_from_db()
        self.assertEqual(task.status, Task.STATUS_TODO)

    def test_student_can_submit_only_one_valid_peer_review_per_peer(self):
        self.login(self.student)
        criteria = {
            f'criterion_{criterion}': 8
            for criterion in self.assignment.peer_evaluation_criteria
        }
        response = self.client.post(
            reverse('peer_review_create', args=[self.assignment.pk]),
            {
                'evaluated': self.student.pk,
                'comment': 'Self evaluation.',
                **criteria,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PeerReview.objects.count(), 0)
        response = self.client.post(
            reverse('peer_review_create', args=[self.assignment.pk]),
            {
                'evaluated': self.peer.pk,
                'comment': 'Good collaboration.',
                **criteria,
            },
        )
        self.assertRedirects(response, reverse('dashboard'))
        review = PeerReview.objects.get()
        self.assertEqual(review.criteria_scores, {
            criterion: 8 for criterion in self.assignment.peer_evaluation_criteria
        })
        self.assertEqual(review.score, 8)
        self.assertFalse(Notification.objects.filter(user=self.peer).exists())

        response = self.client.post(
            reverse('peer_review_create', args=[self.assignment.pk]),
            {
                'evaluated': self.peer.pk,
                'comment': 'Duplicate review.',
                **criteria,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PeerReview.objects.count(), 1)

    def test_peer_evaluation_criteria_are_configurable_and_required_when_enabled(self):
        self.login(self.lecturer)
        response = self.client.get(reverse('assignment_edit', args=[self.assignment.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Meeting internal deadlines')

        form_data = {
            'course': self.course.pk,
            'title': self.assignment.title,
            'description': self.assignment.description,
            'objectives': self.assignment.objectives or 'Objectives.',
            'published_at': timezone.localtime(
                self.assignment.published_at
            ).strftime('%Y-%m-%dT%H:%M'),
            'deadline': timezone.localtime(self.assignment.deadline).strftime('%Y-%m-%dT%H:%M'),
            'status': self.assignment.status,
            'min_group_size': self.assignment.min_group_size,
            'max_group_size': self.assignment.max_group_size,
            'peer_evaluation_enabled': 'on',
        }
        form = AssignmentForm(
            data=form_data,
            instance=self.assignment,
            lecturer=self.lecturer,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('peer_evaluation_criteria', form.errors)
        form_data.update({
            'peer_evaluation_criteria': ['participation', 'communication'],
        })
        form = AssignmentForm(
            data=form_data,
            instance=self.assignment,
            lecturer=self.lecturer,
        )
        self.assertTrue(form.is_valid(), form.errors)
        configured_assignment = form.save()
        self.assertEqual(
            configured_assignment.peer_evaluation_criteria,
            ['participation', 'communication'],
        )

    def test_peer_evaluation_report_is_confidential_and_scoped_to_authorised_lecturers(self):
        review = PeerReview.objects.create(
            assignment=self.assignment,
            evaluator=self.student,
            evaluated=self.peer,
            score=8,
            criteria_scores={
                criterion: 8
                for criterion in self.assignment.peer_evaluation_criteria
            },
            comment='Confidential collaboration feedback.',
        )
        report_url = reverse(
            'peer_evaluation_report',
            args=[self.assignment.pk],
        )
        self.login(self.lecturer)
        response = self.client.get(report_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Confidential collaboration feedback.')
        self.assertContains(response, 'Communication')
        self.assertContains(response, '8.0/10')
        self.assertNotContains(response, self.student.username)

        self.login(self.student)
        self.assertEqual(self.client.get(report_url).status_code, 403)
        self.assertEqual(
            self.client.get(reverse('peer_review_create', args=[self.assignment.pk])).status_code,
            200,
        )
        self.assertNotContains(
            self.client.get(reverse('dashboard')),
            'Confidential collaboration feedback.',
        )

        self.login(self.other_lecturer)
        self.assertEqual(self.client.get(report_url).status_code, 403)

        review.refresh_from_db()
        self.assertEqual(review.evaluator, self.student)

    def test_evidence_upload_and_download_are_limited_to_group_access(self):
        task = Task.objects.create(
            group=self.group,
            description='Submit design',
            assigned_to=self.student,
            deadline=timezone.now() + timedelta(days=2),
        )
        with tempfile.TemporaryDirectory() as media_dir:
            with override_settings(MEDIA_ROOT=Path(media_dir)):
                self.login(self.student)
                response = self.client.post(
                    reverse('task_detail', args=[task.pk]),
                    {
                        'upload_evidence': '1',
                        'evidence_file': SimpleUploadedFile(
                            'design.txt',
                            b'design',
                            content_type='text/plain',
                        ),
                    },
                )
                self.assertRedirects(response, reverse('task_detail', args=[task.pk]))
                evidence = FileDocument.objects.get(task=task)
                download = self.client.get(reverse('evidence_download', args=[evidence.pk]))
                self.assertEqual(download.status_code, 200)
                self.assertEqual(b''.join(download.streaming_content), b'design')
                download.close()
                self.assertEqual(self.client.get(evidence.file.url).status_code, 404)

                self.login(self.outsider)
                download = self.client.get(reverse('evidence_download', args=[evidence.pk]))
                self.assertEqual(download.status_code, 403)

    def test_submission_download_is_limited_to_group_and_course_access(self):
        with tempfile.TemporaryDirectory() as media_dir:
            with override_settings(MEDIA_ROOT=Path(media_dir)):
                submission = Submission.objects.create(
                    group=self.group,
                    submitted_by=self.student,
                    file=SimpleUploadedFile('final.txt', b'final submission'),
                )
                self.login(self.student)
                response = self.client.get(
                    reverse('submission_download', args=[submission.pk])
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(b''.join(response.streaming_content), b'final submission')
                response.close()

                self.login(self.lecturer)
                response = self.client.get(
                    reverse('submission_download', args=[submission.pk])
                )
                self.assertEqual(response.status_code, 200)
                response.close()

                self.login(self.outsider)
                response = self.client.get(
                    reverse('submission_download', args=[submission.pk])
                )
                self.assertEqual(response.status_code, 403)

    def test_user_can_mark_only_their_own_notification_read(self):
        notification = Notification.objects.create(user=self.student, message='Task assigned.')
        other_notification = Notification.objects.create(user=self.peer, message='Private.')
        self.login(self.student)
        response = self.client.post(reverse('notifications'), {
            'notification_id': notification.pk,
        })
        self.assertRedirects(response, reverse('notifications'))
        notification.refresh_from_db()
        self.assertTrue(notification.read)
        response = self.client.post(reverse('notifications'), {
            'notification_id': other_notification.pk,
        })
        self.assertEqual(response.status_code, 404)

    def test_notification_history_links_targets_shows_unread_count_and_marks_all_read(self):
        notification = Notification.objects.create(
            user=self.student,
            message='A task is approaching its deadline.',
            task=Task.objects.create(
                group=self.group,
                title='Linked task',
                description='Task description',
                assigned_to=self.student,
                deadline=timezone.now() + timedelta(days=2),
            ),
            notification_type=Notification.TYPE_DEADLINE,
        )
        self.login(self.student)
        response = self.client.get(reverse('notifications'))
        self.assertContains(response, 'A task is approaching its deadline.')
        self.assertContains(response, 'Notifications')
        self.assertContains(response, reverse('task_detail', args=[notification.task_id]))
        self.assertContains(response, '1</span>')

        response = self.client.post(reverse('notifications'), {'mark_all_read': '1'})
        self.assertRedirects(response, reverse('notifications'))
        notification.refresh_from_db()
        self.assertTrue(notification.read)
        response = self.client.get(reverse('notifications'))
        self.assertNotContains(response, 'text-bg-warning')

    def test_deadline_reminders_are_scheduled_and_deduplicated_for_students_and_lecturer(self):
        self.assignment.deadline = timezone.now() + timedelta(minutes=30)
        self.assignment.save()
        task = Task.objects.create(
            group=self.group,
            title='Internal deadline task',
            description='Finish the draft',
            assigned_to=self.student,
            deadline=timezone.now() + timedelta(minutes=20),
        )

        call_command(
            'send_deadline_reminders',
            hours_before='1',
            window_minutes=60,
        )
        self.assertTrue(Notification.objects.filter(
            user=self.student,
            task=task,
            notification_type=Notification.TYPE_DEADLINE,
            message__contains='approaching',
        ).exists())
        self.assertTrue(Notification.objects.filter(
            user=self.student,
            assignment=self.assignment,
            notification_type=Notification.TYPE_DEADLINE,
            message__contains='Assignment deadline approaching',
        ).exists())
        before_repeat = Notification.objects.count()
        call_command(
            'send_deadline_reminders',
            hours_before='1',
            window_minutes=60,
        )
        self.assertEqual(Notification.objects.count(), before_repeat)

        task.deadline = timezone.now() - timedelta(minutes=5)
        task.save()
        call_command('send_deadline_reminders', hours_before='')
        self.assertTrue(Notification.objects.filter(
            user=self.student,
            task=task,
            message__contains='Task overdue',
        ).exists())
        self.assertTrue(Notification.objects.filter(
            user=self.lecturer,
            task=task,
            message__contains='Task overdue',
        ).exists())
        before_repeat = Notification.objects.count()
        call_command('send_deadline_reminders', hours_before='')
        self.assertEqual(Notification.objects.count(), before_repeat)

    def test_scheduled_assignment_publication_notifies_enrolled_students_when_due(self):
        scheduled = Assignment.objects.create(
            course=self.course,
            title='Scheduled publication',
            description='Scheduled instructions',
            deadline=timezone.now() + timedelta(days=10),
            status=Assignment.STATUS_PUBLISHED,
            published_at=timezone.now() + timedelta(days=1),
        )
        call_command('send_deadline_reminders', hours_before='')
        self.assertFalse(Notification.objects.filter(
            assignment=scheduled,
            user=self.student,
            notification_type=Notification.TYPE_ASSIGNMENT,
        ).exists())

        Assignment.objects.filter(pk=scheduled.pk).update(
            published_at=timezone.now() - timedelta(minutes=1),
        )
        call_command('send_deadline_reminders', hours_before='')
        self.assertTrue(Notification.objects.filter(
            assignment=scheduled,
            user=self.student,
            notification_type=Notification.TYPE_ASSIGNMENT,
        ).exists())

    def test_role_profiles_are_created_and_keep_student_details(self):
        profile = StudentProfile.objects.get(user=self.student)
        self.assertIsNone(profile.student_number)
        self.assertEqual(self.student.student_number, '')
        self.assertTrue(LecturerProfile.objects.filter(user=self.lecturer).exists())
        self.assertEqual(self.course.legacy_semester_record.name, self.course.legacy_semester)
        self.assertEqual(
            self.course.legacy_semester_record.academic_session.name,
            self.course.legacy_academic_year,
        )

    def test_duplicate_group_memberships_are_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            GroupMembership.objects.bulk_create([
                GroupMembership(group=self.group, student=self.student),
            ])

    def test_task_assignee_must_be_an_approved_group_member(self):
        with self.assertRaises(ValidationError):
            Task.objects.create(
                group=self.group,
                description='Invalid assignment',
                assigned_to=self.outsider,
                deadline=timezone.now() + timedelta(days=2),
            )

    def test_task_reassignment_updates_the_task_assignment_relation(self):
        task = Task.objects.create(
            group=self.group,
            description='Share research',
            assigned_to=self.student,
            deadline=timezone.now() + timedelta(days=2),
        )
        task.assignees.add(self.peer)
        task.assigned_to = self.peer
        task.save()
        self.assertSetEqual(
            set(task.assignees.values_list('pk', flat=True)),
            {self.peer.pk},
        )

    def test_group_submission_and_resubmission_keep_one_latest_record(self):
        with tempfile.TemporaryDirectory() as media_dir:
            with override_settings(MEDIA_ROOT=Path(media_dir)):
                self.login(self.student)
                for filename in ('first.txt', 'second.txt'):
                    response = self.client.post(
                        reverse('group_detail', args=[self.group.pk]),
                        {
                            'submit_assignment': '1',
                            'notes': 'Final work',
                            'moodle_reference': 'https://moodle.example.edu/mod/assign/view.php?id=75',
                            'file': SimpleUploadedFile(filename, b'group submission'),
                        },
                    )
                    self.assertEqual(response.status_code, 302)
                submissions = Submission.objects.filter(group=self.group)
                self.assertEqual(submissions.count(), 2)
                self.assertEqual(submissions.filter(is_latest=True).count(), 1)
                self.assertEqual(submissions.filter(is_latest=True).get().assignment, self.assignment)
                self.assertEqual(submissions.filter(status=Submission.STATUS_RESUBMITTED).count(), 0)
                self.assertTrue(all(
                    submission.moodle_reference
                    == 'https://moodle.example.edu/mod/assign/view.php?id=75'
                    for submission in submissions
                ))
                self.assertTrue(Notification.objects.filter(
                    user=self.student,
                    group=self.group,
                    notification_type=Notification.TYPE_SUBMISSION,
                    message__contains='Submission recorded',
                ).exists())
                self.assertTrue(Notification.objects.filter(
                    user=self.lecturer,
                    group=self.group,
                    notification_type=Notification.TYPE_SUBMISSION,
                ).exists())
                self.login(self.student)
                response = self.client.get(reverse('submission_list'))
                self.assertContains(response, 'Group Project')
                self.assertContains(response, 'Previous version')
                self.assertNotContains(response, 'Private Team')
                response = self.client.get(reverse(
                    'submission_receipt',
                    args=[submissions.filter(is_latest=True).get().pk],
                ))
                self.assertContains(response, 'Open recorded Moodle reference')

    def test_submission_receipt_hides_unpublished_feedback_then_shows_released_feedback(self):
        with tempfile.TemporaryDirectory() as media_dir:
            with override_settings(MEDIA_ROOT=Path(media_dir)):
                submission = Submission.objects.create(
                    group=self.group,
                    submitted_by=self.student,
                    file=SimpleUploadedFile('review.txt', b'group submission'),
                )
                self.login(self.lecturer)
                response = self.client.post(
                    reverse('group_detail', args=[self.group.pk]),
                    {
                        'save_grade': '1',
                        'score': '75',
                        'max_score': '100',
                        'feedback': 'Private feedback until release.',
                    },
                )
                self.assertRedirects(
                    response,
                    reverse('group_detail', args=[self.group.pk]),
                )
                submission.refresh_from_db()
                self.assertEqual(submission.status, Submission.STATUS_UNDER_REVIEW)

                self.login(self.student)
                response = self.client.get(reverse(
                    'submission_receipt',
                    args=[submission.pk],
                ))
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, 'Private feedback until release.')
                self.assertNotContains(response, '75/100')

                self.login(self.lecturer)
                self.client.post(
                    reverse('group_detail', args=[self.group.pk]),
                    {
                        'save_grade': '1',
                        'score': '75',
                        'max_score': '100',
                        'feedback': 'Private feedback until release.',
                        'release_now': 'on',
                    },
                )
                submission.refresh_from_db()
                self.assertEqual(
                    submission.status,
                    Submission.STATUS_FEEDBACK_PUBLISHED,
                )
                self.login(self.student)
                response = self.client.get(reverse(
                    'submission_receipt',
                    args=[submission.pk],
                ))
                self.assertContains(response, 'Private feedback until release.')
                self.assertContains(response, 'Lecturer feedback published')

    def test_lecturer_submission_list_is_scoped_to_managed_courses(self):
        other_assignment = Assignment.objects.create(
            course=self.other_course,
            title='Other lecturer private assignment',
            description='Not in this lecturer scope',
            deadline=timezone.now() + timedelta(days=5),
        )
        other_group = Group.objects.create(
            assignment=other_assignment,
            name='Other lecturer private group',
        )
        CourseEnrolment.objects.create(
            course=self.other_course,
            student=self.unassigned_student,
        )
        GroupMembership.objects.create(group=other_group, student=self.unassigned_student)
        with tempfile.TemporaryDirectory() as media_dir:
            with override_settings(MEDIA_ROOT=Path(media_dir)):
                Submission.objects.create(
                    group=other_group,
                    submitted_by=self.unassigned_student,
                    file=SimpleUploadedFile('private.txt', b'private submission'),
                )
                self.login(self.lecturer)
                response = self.client.get(reverse('submission_list'))
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, 'Other lecturer private assignment')

                self.login(self.other_lecturer)
                response = self.client.get(reverse('submission_list'))
                self.assertContains(response, 'Other lecturer private assignment')

                self.login(self.student)
                response = self.client.get(reverse('submission_list'))
                self.assertNotContains(response, 'Other lecturer private assignment')
                self.assertContains(response, 'Team Alpha')
                self.assertContains(response, 'Not Submitted')
                self.assertNotContains(response, 'Other lecturer private group')

    def test_releasing_lecturer_feedback_notifies_group_members_with_group_link(self):
        with tempfile.TemporaryDirectory() as media_dir:
            with override_settings(MEDIA_ROOT=Path(media_dir)):
                submission = Submission.objects.create(
                    group=self.group,
                    submitted_by=self.student,
                    file=SimpleUploadedFile('graded.txt', b'group submission'),
                )
                self.login(self.lecturer)
                response = self.client.post(
                    reverse('group_detail', args=[self.group.pk]),
                    {
                        'save_grade': '1',
                        'score': '80',
                        'max_score': '100',
                        'feedback': 'Good work.',
                        'release_now': 'on',
                    },
                )
                self.assertRedirects(
                    response,
                    reverse('group_detail', args=[self.group.pk]),
                )
                feedback_notifications = Notification.objects.filter(
                    user__in=(self.student, self.peer),
                    group=self.group,
                    notification_type=Notification.TYPE_FEEDBACK,
                )
                self.assertEqual(feedback_notifications.count(), 2)
                self.assertEqual(
                    feedback_notifications.first().get_target_url(),
                    reverse('group_detail', args=[self.group.pk]),
                )

    def test_late_submission_rejection_preserves_existing_latest_submission(self):
        with tempfile.TemporaryDirectory() as media_dir:
            with override_settings(MEDIA_ROOT=Path(media_dir)):
                existing = Submission.objects.create(
                    group=self.group,
                    submitted_by=self.student,
                    file=SimpleUploadedFile('existing.txt', b'previous'),
                )
                self.assignment.deadline = timezone.now() - timedelta(days=1)
                self.assignment.allow_late_submissions = False
                self.assignment.save()
                self.login(self.student)
                response = self.client.post(
                    reverse('group_detail', args=[self.group.pk]),
                    {
                        'submit_assignment': '1',
                        'notes': 'Late work',
                        'file': SimpleUploadedFile('late.txt', b'late'),
                    },
                )
                self.assertEqual(response.status_code, 302)
                existing.refresh_from_db()
                self.assertTrue(existing.is_latest)
                self.assertEqual(Submission.objects.filter(group=self.group, is_latest=True).count(), 1)

    def test_password_reset_flow_sends_single_use_link_and_enforces_validators(self):
        self.student.email = 'student@example.edu'
        self.student.save(update_fields=['email'])
        with override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'):
            response = self.client.post(reverse('password_reset'), {'email': self.student.email})
            self.assertRedirects(response, reverse('password_reset_done'))
            self.assertEqual(len(mail.outbox), 1)
            reset_path = re.search(r'http://testserver(\S+)', mail.outbox[0].body).group(1)

            response = self.client.get(reset_path, follow=True)
            self.assertEqual(response.status_code, 200)
            response = self.client.post(response.request['PATH_INFO'], {
                'new_password1': '123',
                'new_password2': '123',
            })
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'too short', status_code=200)

            response = self.client.post(response.request['PATH_INFO'], {
                'new_password1': 'A-Strong-Unique-Password-932!',
                'new_password2': 'A-Strong-Unique-Password-932!',
            })
            self.assertEqual(response.status_code, 302)
            self.student.refresh_from_db()
            self.assertTrue(self.student.check_password('A-Strong-Unique-Password-932!'))

    def test_peer_evaluation_setting_is_enforced(self):
        self.assignment.peer_evaluation_enabled = False
        self.assignment.save()
        self.login(self.student)
        response = self.client.post(
            reverse('peer_review_create', args=[self.assignment.pk]),
            {'evaluated': self.peer.pk, 'score': 8, 'comment': 'Not enabled.'},
        )
        self.assertRedirects(response, reverse('dashboard'))
        self.assertEqual(PeerReview.objects.count(), 0)

    def test_contribution_events_preserve_actor_and_student_attribution_and_are_immutable(self):
        task = Task.objects.create(
            group=self.group,
            title='Prepare outline',
            description='Draft the outline',
            assigned_to=self.student,
            deadline=timezone.now() + timedelta(days=2),
        )
        record_activity(
            self.lecturer,
            'task_assigned',
            'Assigned Prepare outline',
            assignment=self.assignment,
            group=self.group,
            task=task,
            contributor=self.student,
        )

        activity = ContributionActivity.objects.get(
            event_type='task_assigned',
            task=task,
        )
        self.assertEqual(activity.student, self.student)
        self.assertEqual(activity.performed_by, self.lecturer)
        self.assertEqual(activity.group, self.group)
        self.assertEqual(activity.assignment, self.assignment)
        self.assertIsNotNone(activity.occurred_at)
        activity.description = 'Edited contribution'
        with self.assertRaises(ValidationError):
            activity.save()

    def test_lecturer_contribution_report_is_scoped_and_exports_csv(self):
        task = Task.objects.create(
            group=self.group,
            title='Prepare outline',
            description='Draft the outline',
            assigned_to=self.student,
            deadline=timezone.now() + timedelta(days=2),
        )
        record_activity(
            self.student,
            'task_completed',
            'Completed Prepare outline',
            assignment=self.assignment,
            group=self.group,
            task=task,
        )
        other_assignment = Assignment.objects.create(
            course=self.other_course,
            title='Other lecturer assignment',
            description='Not in this lecturer scope',
            deadline=timezone.now() + timedelta(days=5),
        )
        Group.objects.create(
            assignment=other_assignment,
            name='Other lecturer team',
        )
        self.login(self.lecturer)
        response = self.client.get(reverse('contribution_report'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Team Alpha')
        self.assertContains(response, 'student')
        self.assertNotContains(response, 'Other lecturer team')
        self.assertContains(response, 'supporting evidence')

        response = self.client.get(
            reverse('contribution_report'),
            {'format': 'csv'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertIn(b'BIT326', response.content)
        self.assertIn(b'student', response.content)

        self.login(self.student)
        response = self.client.get(reverse('contribution_report'))
        self.assertEqual(response.status_code, 403)

    def test_contribution_csv_escapes_spreadsheet_formula_cells(self):
        formula_assignment = Assignment.objects.create(
            course=self.course,
            title='Formula-safe export',
            description='CSV formula escaping validation',
            deadline=timezone.now() + timedelta(days=4),
        )
        formula_group = Group.objects.create(
            assignment=formula_assignment,
            name='  =1+1',
        )
        GroupMembership.objects.create(
            group=formula_group,
            student=self.student,
        )
        self.login(self.lecturer)
        response = self.client.get(
            reverse('contribution_report'),
            {'format': 'csv'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"'  =1+1", response.content)

    def test_student_dashboard_only_shows_own_contribution_events(self):
        record_activity(
            self.student,
            'student_action',
            'My contribution event',
            assignment=self.assignment,
            group=self.group,
        )
        record_activity(
            self.peer,
            'student_action',
            'Peer private activity',
            assignment=self.assignment,
            group=self.group,
        )
        self.login(self.student)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'My contribution event')
        self.assertNotContains(response, 'Peer private activity')

    def test_role_scoped_group_progress_report_and_csv_export(self):
        Task.objects.create(
            group=self.group,
            title='Completed item',
            description='Completed item',
            assigned_to=self.student,
            deadline=timezone.now() + timedelta(days=1),
            status=Task.STATUS_COMPLETED,
        )
        other_assignment = Assignment.objects.create(
            course=self.other_course,
            title='Private assignment',
            description='Private',
            deadline=timezone.now() + timedelta(days=5),
        )
        other_group = Group.objects.create(
            assignment=other_assignment,
            name='Other lecturer team',
        )
        self.login(self.lecturer)
        response = self.client.get(
            reverse('reports'),
            {'type': 'progress', 'group': self.group.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['rows']), 1)
        self.assertEqual(response.context['rows'][0][2], 'Team Alpha')
        self.assertEqual(response.context['rows'][0][7], 100)
        self.assertNotIn(other_group.name, str(response.context['rows']))

        response = self.client.get(
            reverse('reports'),
            {'type': 'progress', 'group': self.group.pk, 'format': 'csv'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Group,Members,Tasks,Completed,Overdue,Progress %', response.content)
        self.assertIn(b'Team Alpha', response.content)
        self.assertNotIn(b'Other lecturer team', response.content)

        response = self.client.get(
            reverse('reports'),
            {'type': 'progress', 'group': other_group.pk},
        )
        self.assertEqual(response.status_code, 403)
        response = self.client.get(
            reverse('reports'),
            {'type': 'progress', 'group': f'{self.group.pk} OR 1=1'},
        )
        self.assertEqual(response.status_code, 403)

    def test_student_reports_show_only_own_authorised_group_data(self):
        task = Task.objects.create(
            group=self.group,
            title='My task',
            description='My task',
            assigned_to=self.student,
            deadline=timezone.now() + timedelta(days=1),
        )
        Task.objects.create(
            group=self.group,
            title='Peer task',
            description='Peer task',
            assigned_to=self.peer,
            deadline=timezone.now() + timedelta(days=1),
        )
        record_activity(
            self.student,
            'task_created',
            'Student action',
            assignment=self.assignment,
            group=self.group,
            task=task,
        )
        self.login(self.student)
        response = self.client.get(reverse('reports'), {'type': 'student_tasks'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['rows']), 1)
        self.assertEqual(response.context['rows'][0][3], 'My task')
        self.assertNotContains(response, 'Peer task')

        response = self.client.get(reverse('reports'), {'type': 'contribution'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['rows']), 1)
        self.assertEqual(response.context['rows'][0][3], self.student.username)

        response = self.client.get(reverse('reports'), {'type': 'admin_activity'})
        self.assertEqual(response.status_code, 403)

    def test_admin_course_summary_and_activity_report(self):
        self.login(self.administrator)
        response = self.client.get(reverse('reports'), {'type': 'course_summary'})
        self.assertEqual(response.status_code, 200)
        course_row = next(row for row in response.context['rows'] if row[0] == 'BIT326')
        self.assertEqual(course_row[3], 1)

        response = self.client.get(reverse('reports'), {'type': 'admin_activity'})
        self.assertEqual(response.status_code, 200)

    def test_all_authorised_report_types_render(self):
        self.login(self.lecturer)
        for report_type in (
            'submission',
            'progress',
            'contribution',
            'inactive',
            'student_tasks',
            'performance',
            'course_summary',
        ):
            with self.subTest(report_type=report_type):
                response = self.client.get(
                    reverse('reports'),
                    {
                        'type': report_type,
                        'course': self.course.pk,
                        'assignment': self.assignment.pk,
                        'group': self.group.pk,
                        'semester': self.course.semester,
                        'year_of_study': self.course.year_of_study,
                    },
                )
                self.assertEqual(response.status_code, 200)

        self.login(self.administrator)
        response = self.client.get(
            reverse('reports'),
            {'type': 'admin_activity'},
        )
        self.assertEqual(response.status_code, 200)
