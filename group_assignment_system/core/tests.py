import re
import tempfile
from datetime import timedelta
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core import mail
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    AcademicSession,
    Assignment,
    AuditLog,
    Course,
    CourseEnrolment,
    ContributionActivity,
    FileDocument,
    Group,
    GroupMembership,
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
    User,
)
from .forms import AssignmentForm, GroupDocumentUploadForm
from .services import record_activity


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
        cls.course = Course.objects.create(
            lecturer=cls.lecturer,
            name='BIT326',
            code='BIT326',
            semester='Fall',
            academic_year='2026',
        )
        cls.semester = cls.course.semester_record
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
            semester='Fall',
            academic_year='2026',
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
            'semester': 'Fall',
            'academic_year': '2026',
        })
        self.assertEqual(response.status_code, 403)
        self.assertNotEqual(self.client.get(reverse('admin:index')).status_code, 200)

    def test_student_api_is_read_scoped_and_cannot_mutate(self):
        self.login(self.student)
        response = self.client.get(reverse('api-groups-list'))
        self.assertEqual(response.status_code, 200)
        group_names = [group['name'] for group in response.data]
        self.assertEqual(group_names, ['Team Alpha'])

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
        response = self.client.get(reverse('api-assignments-list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['id'] for item in response.data], [self.assignment.pk])

    def test_group_and_task_navigation_pages_are_role_scoped(self):
        Task.objects.create(
            group=self.group,
            title='Team planning',
            description='Prepare the team plan',
            assigned_to=self.student,
            deadline=timezone.now() + timedelta(days=1),
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
        self.assertContains(response, 'Team planning')
        self.assertNotContains(response, 'Private planning')
        dashboard = self.client.get(reverse('dashboard'))
        self.assertContains(dashboard, 'Group workspace')
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
        self.assertContains(response, 'Users &amp; administration')

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
        response = self.client.post(reverse('course_create'), {
            'name': 'BIT327',
            'code': 'BIT327',
            'programme': 'BIT',
            'semester_record': self.semester.pk,
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

    def test_administrator_manages_academic_years_semesters_courses_and_roster(self):
        self.login(self.administrator)
        response = self.client.post(reverse('academic_calendar'), {
            'session-name': '2027',
            'session-start_date': '2027-01-01',
            'session-end_date': '2027-12-31',
            'session-is_active': 'on',
            'create_session': '1',
        })
        self.assertRedirects(response, reverse('academic_calendar'))
        session = AcademicSession.objects.get(name='2027')

        response = self.client.post(reverse('academic_calendar'), {
            'semester-name': 'Spring',
            'semester-academic_session': session.pk,
            'semester-is_active': 'on',
            'create_semester': '1',
        })
        self.assertRedirects(response, reverse('academic_calendar'))
        semester = Semester.objects.get(name='Spring', academic_session=session)

        response = self.client.post(reverse('course_create'), {
            'code': 'BIT327',
            'name': 'Software Engineering',
            'programme': 'BIT',
            'semester_record': semester.pk,
            'lecturer': self.lecturer.pk,
            'is_active': 'on',
        })
        course = Course.objects.get(code='BIT327')
        self.assertRedirects(response, reverse('course_detail', args=[course.pk]))
        self.assertEqual(course.academic_year, '2027')
        self.assertEqual(course.semester, 'Spring')

        CourseEnrolment.objects.create(course=course, student=self.student)
        response = self.client.get(reverse('course_detail', args=[course.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.student.username)

        response = self.client.post(reverse('academic_session_edit', args=[session.pk]), {
            'name': '2027/28',
            'start_date': '2027-01-01',
            'end_date': '2027-12-31',
            'is_active': 'on',
        })
        self.assertRedirects(response, reverse('academic_calendar'))
        course.refresh_from_db()
        self.assertEqual(course.academic_year, '2027/28')

        response = self.client.post(reverse('course_edit', args=[course.pk]), {
            'code': 'BIT328',
            'name': 'Advanced Software Engineering',
            'programme': 'BIT',
            'semester_record': self.semester.pk,
            'lecturer': self.other_lecturer.pk,
        })
        self.assertRedirects(response, reverse('course_detail', args=[course.pk]))
        course.refresh_from_db()
        self.assertEqual(course.code, 'BIT328')
        self.assertEqual(course.lecturer, self.other_lecturer)
        self.assertFalse(course.is_active)

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
        response = self.client.post(reverse('task_create', args=[self.group.pk]), {
            'title': 'Design',
            'description': 'Prepare the system design',
            'assigned_to': self.peer.pk,
            'deadline': (timezone.now() + timedelta(days=3)).strftime('%Y-%m-%dT%H:%M'),
        })
        task = Task.objects.get(description='Prepare the system design')
        self.assertRedirects(response, reverse('task_detail', args=[task.pk]))
        self.assertEqual(task.assigned_to, self.peer)
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
        self.assertEqual(self.course.semester_record.name, self.course.semester)
        self.assertEqual(
            self.course.semester_record.academic_session.name,
            self.course.academic_year,
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
                        'semester': self.semester.name,
                        'academic_year': self.course.academic_year,
                    },
                )
                self.assertEqual(response.status_code, 200)

        self.login(self.administrator)
        response = self.client.get(
            reverse('reports'),
            {'type': 'admin_activity'},
        )
        self.assertEqual(response.status_code, 200)
