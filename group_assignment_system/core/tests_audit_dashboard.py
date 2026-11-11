import csv
from datetime import timedelta
from io import StringIO

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    AcademicSession,
    Assignment,
    AssignmentClassRepresentative,
    Course,
    CourseEnrolment,
    Group,
    GroupMembership,
    GroupMembershipEvent,
    Semester,
    User,
)
from .services import appoint_assignment_class_representative


class GroupAuditDashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.lecturer = User.objects.create_user(
            username='audit-lecturer',
            password='test-password',
            role=User.ROLE_LECTURER,
        )
        cls.other_lecturer = User.objects.create_user(
            username='audit-other-lecturer',
            password='test-password',
            role=User.ROLE_LECTURER,
        )
        cls.administrator = User.objects.create_user(
            username='audit-admin',
            password='test-password',
            role=User.ROLE_ADMIN,
        )
        cls.student = User.objects.create_user(
            username='audit-student',
            password='test-password',
            role=User.ROLE_STUDENT,
        )
        cls.representative = User.objects.create_user(
            username='audit-representative',
            password='test-password',
            role=User.ROLE_STUDENT,
        )
        cls.session = AcademicSession.objects.create(
            name='Audit 2026',
            start_date='2026-01-01',
            end_date='2026-12-31',
        )
        cls.semester = Semester.objects.create(
            name='Audit Semester',
            academic_session=cls.session,
        )
        cls.course = cls.make_course(
            cls.lecturer,
            'AUD301',
            'Audit Course One',
            3,
            1,
        )
        cls.second_course = cls.make_course(
            cls.lecturer,
            'AUD302',
            'Audit Course Two',
            3,
            2,
        )
        cls.other_course = cls.make_course(
            cls.other_lecturer,
            'AUD401',
            'Other Lecturer Course',
            4,
            1,
        )
        cls.assignment = cls.make_assignment(cls.course, 'Audit Assignment One')
        cls.second_assignment = cls.make_assignment(
            cls.second_course,
            'Audit Assignment Two',
        )
        cls.other_assignment = cls.make_assignment(
            cls.other_course,
            'Private Audit Assignment',
        )
        cls.group = Group.objects.create(
            assignment=cls.assignment,
            name='Audit Group One',
        )
        cls.destination_group = Group.objects.create(
            assignment=cls.assignment,
            name='Audit Group Two',
        )
        cls.second_group = Group.objects.create(
            assignment=cls.second_assignment,
            name='Audit Group Three',
        )
        cls.other_group = Group.objects.create(
            assignment=cls.other_assignment,
            name='Private Audit Group',
        )
        CourseEnrolment.objects.create(course=cls.course, student=cls.student)
        CourseEnrolment.objects.create(
            course=cls.course,
            student=cls.representative,
        )
        student_profile = cls.student.student_profile
        student_profile.student_number = 'AUD-STUDENT-01'
        student_profile.save(update_fields=['student_number'])

    @classmethod
    def make_course(cls, lecturer, code, name, year, semester):
        return Course.objects.create(
            lecturer=lecturer,
            code=code,
            name=name,
            year_of_study=year,
            semester=semester,
            legacy_semester_record=cls.semester,
        )

    @staticmethod
    def make_assignment(course, title):
        return Assignment.objects.create(
            course=course,
            title=title,
            description='Audit test assignment',
            deadline=timezone.now() + timedelta(days=30),
        )

    def make_event(
        self,
        action,
        *,
        assignment=None,
        group=None,
        student=None,
        actor=None,
        previous_group=None,
    ):
        return GroupMembershipEvent.objects.create(
            assignment=assignment or self.assignment,
            group=group,
            student=student,
            actor=actor or self.lecturer,
            previous_group=previous_group,
            action=action,
        )

    def test_lecturer_and_administrator_access_authorized_events(self):
        event = self.make_event(
            GroupMembershipEvent.ACTION_GROUP_CREATED,
            group=self.group,
        )
        for user in (self.lecturer, self.administrator):
            with self.subTest(role=user.role):
                self.client.force_login(user)
                response = self.client.get(reverse('audit_dashboard'))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'Audit Assignment One')
                self.assertIn(event, response.context['events'])

    def test_students_and_representatives_cannot_access_audit_records(self):
        AssignmentClassRepresentative.objects.create(
            assignment=self.assignment,
            student=self.representative,
            appointed_by=self.lecturer,
        )
        for user in (self.student, self.representative):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(reverse('audit_dashboard'))
                self.assertEqual(response.status_code, 403)

    def test_out_of_scope_assignment_and_group_parameters_do_not_expand_results(self):
        authorized_event = self.make_event(
            GroupMembershipEvent.ACTION_GROUP_CREATED,
            group=self.group,
        )
        self.make_event(
            GroupMembershipEvent.ACTION_GROUP_CREATED,
            assignment=self.other_assignment,
            group=self.other_group,
            actor=self.other_lecturer,
        )
        self.client.force_login(self.lecturer)
        for filters in (
            {'assignment': self.other_assignment.pk},
            {'group': self.other_group.pk},
        ):
            with self.subTest(filters=filters):
                response = self.client.get(reverse('audit_dashboard'), filters)
                self.assertEqual(response.status_code, 200)
                self.assertIn(authorized_event, response.context['events'])
                self.assertTrue(
                    all(
                        event.assignment.course == self.course
                        for event in response.context['events']
                    )
                )
                self.assertNotContains(response, 'Private Audit Assignment')

    def test_course_and_assignment_filters_return_only_matching_events(self):
        first_event = self.make_event(
            GroupMembershipEvent.ACTION_GROUP_CREATED,
            group=self.group,
        )
        second_event = self.make_event(
            GroupMembershipEvent.ACTION_GROUP_CREATED,
            assignment=self.second_assignment,
            group=self.second_group,
        )
        self.client.force_login(self.lecturer)

        response = self.client.get(
            reverse('audit_dashboard'),
            {'course': self.second_course.pk},
        )
        self.assertEqual(list(response.context['events']), [second_event])

        response = self.client.get(
            reverse('audit_dashboard'),
            {'assignment': self.assignment.pk},
        )
        self.assertEqual(list(response.context['events']), [first_event])

    def test_student_id_actor_and_action_filters(self):
        added = self.make_event(
            GroupMembershipEvent.ACTION_ADDED,
            group=self.group,
            student=self.student,
        )
        removed = self.make_event(
            GroupMembershipEvent.ACTION_REMOVED,
            group=self.group,
            student=self.student,
            actor=self.administrator,
        )
        self.client.force_login(self.lecturer)

        response = self.client.get(
            reverse('audit_dashboard'),
            {'student_id': 'AUD-STUDENT-01'},
        )
        self.assertEqual(set(response.context['events']), {added, removed})

        response = self.client.get(
            reverse('audit_dashboard'),
            {'actor': self.administrator.pk},
        )
        self.assertEqual(list(response.context['events']), [removed])

        response = self.client.get(
            reverse('audit_dashboard'),
            {'action': GroupMembershipEvent.ACTION_ADDED},
        )
        self.assertEqual(list(response.context['events']), [added])

    def test_date_range_filter_limits_events_to_selected_days(self):
        event = self.make_event(
            GroupMembershipEvent.ACTION_GROUP_CREATED,
            group=self.group,
        )
        today = timezone.localdate().isoformat()
        yesterday = (timezone.localdate() - timedelta(days=1)).isoformat()
        self.client.force_login(self.lecturer)

        response = self.client.get(
            reverse('audit_dashboard'),
            {'date_from': today, 'date_to': today},
        )
        self.assertEqual(list(response.context['events']), [event])

        response = self.client.get(
            reverse('audit_dashboard'),
            {'date_from': yesterday, 'date_to': yesterday},
        )
        self.assertEqual(list(response.context['events']), [])

    def test_activity_history_is_paginated(self):
        for _ in range(51):
            self.make_event(
                GroupMembershipEvent.ACTION_GROUP_UPDATED,
                group=self.group,
            )
        self.client.force_login(self.lecturer)

        first_page = self.client.get(reverse('audit_dashboard'))
        self.assertEqual(len(first_page.context['events']), 50)
        self.assertEqual(first_page.context['page_obj'].paginator.count, 51)

        second_page = self.client.get(reverse('audit_dashboard'), {'page': 2})
        self.assertEqual(len(second_page.context['events']), 1)

    def test_csv_export_is_limited_to_authorized_staff_and_scoped_events(self):
        self.make_event(
            GroupMembershipEvent.ACTION_GROUP_CREATED,
            group=self.group,
        )
        self.make_event(
            GroupMembershipEvent.ACTION_GROUP_CREATED,
            assignment=self.other_assignment,
            group=self.other_group,
            actor=self.other_lecturer,
        )
        for user in (self.lecturer, self.administrator):
            with self.subTest(role=user.role):
                self.client.force_login(user)
                response = self.client.get(
                    reverse('audit_dashboard'),
                    {'format': 'csv'},
                )
                self.assertEqual(response.status_code, 200)
                content = b''.join(response.streaming_content).decode()
                rows = list(csv.DictReader(StringIO(content)))
                if user == self.lecturer:
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0]['Assignment'], 'Audit Assignment One')
                    self.assertNotIn('Private Audit Assignment', content)
                else:
                    self.assertEqual(len(rows), 2)
                    self.assertIn('Private Audit Assignment', content)

        for user in (self.student, self.representative):
            self.client.force_login(user)
            response = self.client.get(
                reverse('audit_dashboard'),
                {'format': 'csv'},
            )
            self.assertEqual(response.status_code, 403)

    def test_historical_missing_snapshots_display_not_recorded(self):
        event = self.make_event(
            GroupMembershipEvent.ACTION_ADDED,
            group=self.group,
            student=self.student,
        )
        GroupMembershipEvent._base_manager.filter(pk=event.pk).update(
            actor_role_snapshot='',
            course_code_snapshot='',
            course_name_snapshot='',
            assignment_title_snapshot='',
            student_number_snapshot='',
        )
        self.client.force_login(self.lecturer)

        response = self.client.get(reverse('audit_dashboard'))
        self.assertEqual(response.context['lecturer_interventions'], 0)
        self.assertContains(response, 'Not recorded')

        response = self.client.get(
            reverse('audit_dashboard'),
            {'format': 'csv'},
        )
        content = b''.join(response.streaming_content).decode()
        row = next(csv.DictReader(StringIO(content)))
        self.assertEqual(row['Actor role'], 'Not recorded')
        self.assertEqual(row['Course'], 'Not recorded')
        self.assertEqual(row['Assignment'], 'Not recorded')
        self.assertEqual(row['Student ID'], 'Not recorded')

    def test_new_events_capture_available_snapshots(self):
        event = self.make_event(
            GroupMembershipEvent.ACTION_ADDED,
            group=self.group,
            student=self.student,
        )
        self.assertEqual(event.actor_role_snapshot, User.ROLE_LECTURER)
        self.assertEqual(event.course_code_snapshot, self.course.code)
        self.assertEqual(event.course_name_snapshot, self.course.name)
        self.assertEqual(event.assignment_title_snapshot, self.assignment.title)
        self.assertEqual(event.group_name_snapshot, self.group.name)
        self.assertEqual(event.student_number_snapshot, 'AUD-STUDENT-01')

    def test_summary_metrics_count_distinct_recorded_actions(self):
        appointment = AssignmentClassRepresentative.objects.create(
            assignment=self.assignment,
            student=self.representative,
            appointed_by=self.lecturer,
        )
        self.make_event(
            GroupMembershipEvent.ACTION_GROUP_CREATED,
            group=self.group,
        )
        self.make_event(
            GroupMembershipEvent.ACTION_ADDED,
            group=self.group,
            student=self.student,
            actor=self.representative,
        )
        self.make_event(
            GroupMembershipEvent.ACTION_REMOVED,
            group=self.group,
            student=self.student,
        )
        self.make_event(
            GroupMembershipEvent.ACTION_TRANSFERRED,
            group=self.destination_group,
            previous_group=self.group,
            student=self.student,
            actor=self.administrator,
        )
        self.make_event(
            GroupMembershipEvent.ACTION_GROUP_ARCHIVED,
            group=self.group,
        )
        self.make_event(
            GroupMembershipEvent.ACTION_GROUP_UPDATED,
            group=self.group,
            actor=self.administrator,
        )

        self.client.force_login(self.lecturer)
        response = self.client.get(reverse('audit_dashboard'))
        counts = response.context['action_counts']
        self.assertEqual(counts[GroupMembershipEvent.ACTION_GROUP_CREATED], 1)
        self.assertEqual(counts[GroupMembershipEvent.ACTION_ADDED], 1)
        self.assertEqual(counts[GroupMembershipEvent.ACTION_REMOVED], 1)
        self.assertEqual(counts[GroupMembershipEvent.ACTION_TRANSFERRED], 1)
        self.assertEqual(counts[GroupMembershipEvent.ACTION_GROUP_ARCHIVED], 1)
        self.assertEqual(response.context['representative_interventions'], 1)
        self.assertEqual(response.context['lecturer_interventions'], 3)
        self.assertEqual(response.context['administrator_interventions'], 2)
        self.assertEqual(appointment.status, AssignmentClassRepresentative.STATUS_ACTIVE)
        self.assertNotContains(response, 'Membership Additions')


class AssignmentRepresentativeWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.lecturer = User.objects.create_user(
            username='representative-lecturer',
            password='test-password',
            role=User.ROLE_LECTURER,
        )
        cls.other_lecturer = User.objects.create_user(
            username='representative-other-lecturer',
            password='test-password',
            role=User.ROLE_LECTURER,
        )
        cls.administrator = User.objects.create_user(
            username='representative-admin',
            password='test-password',
            role=User.ROLE_ADMIN,
        )
        cls.representative = User.objects.create_user(
            username='rep-one',
            password='test-password',
            role=User.ROLE_STUDENT,
            first_name='Riley',
            last_name='Representative',
        )
        cls.replacement = User.objects.create_user(
            username='rep-two',
            password='test-password',
            role=User.ROLE_STUDENT,
            first_name='Casey',
            last_name='Replacement',
        )
        cls.member = User.objects.create_user(
            username='rep-member',
            password='test-password',
            role=User.ROLE_STUDENT,
            first_name='Morgan',
            last_name='Member',
        )
        cls.unenrolled = User.objects.create_user(
            username='rep-unenrolled',
            password='test-password',
            role=User.ROLE_STUDENT,
        )
        cls.session = AcademicSession.objects.create(
            name='Representative 2026',
            start_date='2026-01-01',
            end_date='2026-12-31',
        )
        cls.semester = Semester.objects.create(
            name='Representative Semester',
            academic_session=cls.session,
        )
        cls.course = Course.objects.create(
            lecturer=cls.lecturer,
            code='REP301',
            name='Representative Course',
            year_of_study=3,
            semester=1,
            legacy_semester_record=cls.semester,
        )
        cls.other_course = Course.objects.create(
            lecturer=cls.other_lecturer,
            code='REP401',
            name='Other Representative Course',
            year_of_study=4,
            semester=1,
            legacy_semester_record=cls.semester,
        )
        cls.assignment = Assignment.objects.create(
            course=cls.course,
            title='Representative Assignment',
            description='Representative workflow test',
            deadline=timezone.now() + timedelta(days=30),
            status=Assignment.STATUS_DRAFT,
        )
        cls.same_course_assignment = Assignment.objects.create(
            course=cls.course,
            title='Different Assignment Same Course',
            description='Must remain separately scoped',
            deadline=timezone.now() + timedelta(days=30),
            status=Assignment.STATUS_DRAFT,
        )
        cls.other_assignment = Assignment.objects.create(
            course=cls.other_course,
            title='Other Lecturer Assignment',
            description='Outside lecturer scope',
            deadline=timezone.now() + timedelta(days=30),
            status=Assignment.STATUS_DRAFT,
        )
        for student in (cls.representative, cls.replacement, cls.member):
            CourseEnrolment.objects.create(course=cls.course, student=student)
        cls.representative.student_profile.student_number = 'REP-001'
        cls.representative.student_profile.save(update_fields=['student_number'])
        cls.replacement.student_profile.student_number = 'REP-002'
        cls.replacement.student_profile.save(update_fields=['student_number'])
        Group.objects.create(assignment=cls.assignment, name='Representative Group')
        Group.objects.create(
            assignment=cls.same_course_assignment,
            name='Other Same-Course Group',
        )

    @staticmethod
    def create_assignment_group(assignment, name):
        return Group.objects.create(assignment=assignment, name=name)

    def appoint_through_page(self, user, student, assignment=None):
        self.client.force_login(user)
        response = self.client.post(
            reverse('assignment_detail', args=[(assignment or self.assignment).pk]),
            {'appoint_representative': '1', 'student': student.pk},
        )
        return response

    def test_manager_page_lists_only_enrolled_active_candidates_with_id_and_name(self):
        self.client.force_login(self.lecturer)
        response = self.client.get(
            reverse('assignment_detail', args=[self.assignment.pk]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'REP-001 — Riley Representative')
        self.assertContains(response, 'REP-002 — Casey Replacement')
        self.assertNotContains(response, 'rep-unenrolled')

    def test_lecturer_can_appoint_and_replace_assignment_representative(self):
        response = self.appoint_through_page(self.lecturer, self.representative)
        self.assertRedirects(
            response,
            reverse('assignment_detail', args=[self.assignment.pk]),
        )
        first = AssignmentClassRepresentative.objects.get(
            assignment=self.assignment,
            status=AssignmentClassRepresentative.STATUS_ACTIVE,
        )
        self.assertEqual(first.student, self.representative)
        self.assertEqual(first.appointed_by, self.lecturer)
        appointment_event = GroupMembershipEvent.objects.get(
            action=GroupMembershipEvent.ACTION_REPRESENTATIVE_APPOINTED,
            representative_appointment=first,
        )
        self.assertEqual(appointment_event.actor, self.lecturer)
        self.assertEqual(appointment_event.actor_role_snapshot, User.ROLE_LECTURER)
        self.assertEqual(appointment_event.course_code_snapshot, self.course.code)

        response = self.appoint_through_page(self.administrator, self.replacement)
        self.assertRedirects(
            response,
            reverse('assignment_detail', args=[self.assignment.pk]),
        )
        first.refresh_from_db()
        second = AssignmentClassRepresentative.objects.get(
            assignment=self.assignment,
            status=AssignmentClassRepresentative.STATUS_ACTIVE,
        )
        self.assertEqual(first.status, AssignmentClassRepresentative.STATUS_REPLACED)
        self.assertEqual(first.ended_by, self.administrator)
        self.assertEqual(second.student, self.replacement)
        self.assertTrue(
            GroupMembershipEvent.objects.filter(
                action=GroupMembershipEvent.ACTION_REPRESENTATIVE_REPLACED,
                representative_appointment=first,
                actor=self.administrator,
            ).exists()
        )

    def test_appointment_rejects_unenrolled_candidates_and_unmanaged_lecturers(self):
        self.client.force_login(self.lecturer)
        response = self.appoint_through_page(self.lecturer, self.unenrolled)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            AssignmentClassRepresentative.objects.filter(
                assignment=self.assignment,
                student=self.unenrolled,
            ).exists()
        )

        self.client.force_login(self.other_lecturer)
        response = self.appoint_through_page(self.other_lecturer, self.representative)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            AssignmentClassRepresentative.objects.filter(
                assignment=self.assignment,
                student=self.representative,
            ).exists()
        )

    def test_student_cannot_appoint_or_revoke_representatives(self):
        appoint_assignment_class_representative(
            self.lecturer,
            self.assignment,
            self.representative,
        )
        self.assignment.status = Assignment.STATUS_PUBLISHED
        self.assignment.save()
        self.client.force_login(self.representative)
        response = self.client.post(
            reverse('assignment_detail', args=[self.assignment.pk]),
            {'appoint_representative': '1', 'student': self.replacement.pk},
        )
        self.assertEqual(response.status_code, 403)
        response = self.client.post(
            reverse('assignment_detail', args=[self.assignment.pk]),
            {'revoke_representative': '1', 'reason': 'Unauthorized'},
        )
        self.assertEqual(response.status_code, 403)

    def test_revocation_removes_group_access_and_records_reason(self):
        appointment = appoint_assignment_class_representative(
            self.lecturer,
            self.assignment,
            self.representative,
        )
        self.client.force_login(self.lecturer)
        response = self.client.post(
            reverse('assignment_detail', args=[self.assignment.pk]),
            {'revoke_representative': '1', 'reason': 'Appointment ended'},
        )
        self.assertRedirects(
            response,
            reverse('assignment_detail', args=[self.assignment.pk]),
        )
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, AssignmentClassRepresentative.STATUS_REMOVED)
        self.assertEqual(appointment.end_reason, 'Appointment ended')
        self.assertEqual(appointment.ended_by, self.lecturer)
        self.assertTrue(
            GroupMembershipEvent.objects.filter(
                action=GroupMembershipEvent.ACTION_REPRESENTATIVE_REVOKED,
                representative_appointment=appointment,
                reason='Appointment ended',
            ).exists()
        )
        self.client.force_login(self.representative)
        self.assertEqual(
            self.client.get(
                reverse('assignment_group_create', args=[self.assignment.pk]),
            ).status_code,
            403,
        )

    def test_representative_is_scoped_to_one_assignment_and_cannot_open_other_group(self):
        appoint_assignment_class_representative(
            self.lecturer,
            self.assignment,
            self.representative,
        )
        self.client.force_login(self.representative)
        self.assertEqual(
            self.client.get(
                reverse('assignment_group_create', args=[self.same_course_assignment.pk]),
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                reverse('group_detail', args=[self.assignment.groups.first().pk]),
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                reverse('group_detail', args=[self.same_course_assignment.groups.first().pk]),
            ).status_code,
            403,
        )
        protected_group = self.same_course_assignment.groups.first()
        protected_membership = GroupMembership.objects.create(
            group=protected_group,
            student=self.member,
        )
        response = self.client.post(
            reverse('group_detail', args=[protected_group.pk]),
            {
                'remove_member': '1',
                'membership_id': protected_membership.pk,
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            GroupMembership.objects.filter(pk=protected_membership.pk).exists()
        )

    def test_lecturer_can_override_representative_group_and_membership_changes(self):
        appoint_assignment_class_representative(
            self.lecturer,
            self.assignment,
            self.representative,
        )
        group = self.create_assignment_group(self.assignment, 'Lecturer Oversight Group')
        membership = GroupMembership.objects.create(
            group=group,
            student=self.member,
        )
        self.client.force_login(self.lecturer)
        response = self.client.post(
            reverse('group_detail', args=[group.pk]),
            {
                'update_group': '1',
                'name': 'Lecturer Corrected Group',
                'description': 'Corrected by lecturer oversight.',
                'max_members': self.assignment.max_group_size,
                'leader': '',
            },
        )
        self.assertRedirects(response, reverse('group_detail', args=[group.pk]))
        group.refresh_from_db()
        self.assertEqual(group.name, 'Lecturer Corrected Group')

        response = self.client.post(
            reverse('group_detail', args=[group.pk]),
            {'remove_member': '1', 'membership_id': membership.pk},
        )
        self.assertRedirects(response, reverse('group_detail', args=[group.pk]))
        self.assertFalse(GroupMembership.objects.filter(pk=membership.pk).exists())
        self.assertTrue(
            GroupMembershipEvent.objects.filter(
                group=group,
                student=self.member,
                actor=self.lecturer,
                action=GroupMembershipEvent.ACTION_REMOVED,
            ).exists()
        )