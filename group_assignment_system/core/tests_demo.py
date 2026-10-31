import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import ContributionActivity, FileDocument, Group, GroupMembership, Notification, Task, User


class DemoDataCommandTests(TestCase):
    def test_seed_command_is_repeatable_and_demo_accounts_can_open_role_pages(self):
        with tempfile.TemporaryDirectory() as media_dir:
            with override_settings(MEDIA_ROOT=Path(media_dir)):
                call_command('seed_demo_data', verbosity=0)
                first_counts = {
                    'users': User.objects.count(),
                    'groups': Group.objects.count(),
                    'memberships': GroupMembership.objects.count(),
                    'tasks': Task.objects.count(),
                    'documents': FileDocument.objects.count(),
                    'contributions': ContributionActivity.objects.count(),
                    'notifications': Notification.objects.count(),
                }

                call_command('seed_demo_data', verbosity=0)
                second_counts = {
                    'users': User.objects.count(),
                    'groups': Group.objects.count(),
                    'memberships': GroupMembership.objects.count(),
                    'tasks': Task.objects.count(),
                    'documents': FileDocument.objects.count(),
                    'contributions': ContributionActivity.objects.count(),
                    'notifications': Notification.objects.count(),
                }
                self.assertEqual(second_counts, first_counts)
                self.assertGreater(first_counts['documents'], 0)
                self.assertGreater(first_counts['contributions'], 0)
                self.assertGreater(first_counts['notifications'], 0)

                role_accounts = (
                    ('admin.demo', 'DemoAdmin123!', 'admin', ('dashboard', 'reports', 'academic_calendar')),
                    ('lecturer.banda', 'DemoLecturer123!', 'lecturer', ('dashboard', 'course_list', 'assignment_list', 'group_list', 'task_list', 'submission_list', 'reports')),
                    ('student01', 'DemoStudent123!', 'student', ('dashboard', 'course_list', 'assignment_list', 'group_list', 'task_list', 'notifications')),
                )
                for username, password, role, pages in role_accounts:
                    with self.subTest(username=username):
                        account = User.objects.get(username=username)
                        self.assertEqual(account.role, role)
                        self.assertTrue(account.check_password(password))
                        self.client.force_login(account)
                        for page in pages:
                            with self.subTest(page=page):
                                self.assertEqual(
                                    self.client.get(reverse(page)).status_code,
                                    200,
                                )
