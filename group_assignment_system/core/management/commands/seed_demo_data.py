"""
Load fictional UNILUS demonstration data.

Safe to run more than once: uses get_or_create / update_or_create keyed on stable usernames
and course/assignment names so duplicates are not created.

Usage (from group_assignment_system/):
    python manage.py seed_demo_data
"""

from datetime import timedelta
from decimal import Decimal

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import (
    ActivityLog,
    Assignment,
    ContributionActivity,
    Course,
    CourseEnrolment,
    FileDocument,
    Grade,
    Group,
    GroupMembership,
    GroupMessage,
    Notification,
    PeerReview,
    StudentProfile,
    Submission,
    Task,
    User,
)


class Command(BaseCommand):
    help = 'Create or refresh fictional demonstration data for UNILUS SGAMS.'

    def handle(self, *args, **options):
        admin, _ = User.objects.update_or_create(
            username='admin.demo',
            defaults={
                'role': User.ROLE_ADMIN,
                'email': 'admin.demo@unilus.example',
                'first_name': 'Ada',
                'last_name': 'Admin',
                'is_staff': True,
                'is_superuser': True,
                'is_active': True,
            },
        )
        if not admin.has_usable_password():
            admin.set_password('DemoAdmin123!')
            admin.save()
        else:
            admin.set_password('DemoAdmin123!')
            admin.save(update_fields=['password'])

        lecturers = []
        for username, first, last in [
            ('lecturer.banda', 'Chanda', 'Banda'),
            ('lecturer.phiri', 'Mwila', 'Phiri'),
        ]:
            user, _ = User.objects.update_or_create(
                username=username,
                defaults={
                    'role': User.ROLE_LECTURER,
                    'email': f'{username}@unilus.example',
                    'first_name': first,
                    'last_name': last,
                    'is_staff': True,
                    'is_active': True,
                },
            )
            user.set_password('DemoLecturer123!')
            user.save()
            lecturers.append(user)

        students = []
        for i in range(1, 13):
            username = f'student{i:02d}'
            user, _ = User.objects.update_or_create(
                username=username,
                defaults={
                    'role': User.ROLE_STUDENT,
                    'email': f'{username}@student.unilus.example',
                    'first_name': f'Student{i:02d}',
                    'last_name': 'Demo',
                    'is_active': True,
                },
            )
            user.set_password('DemoStudent123!')
            user.save()
            StudentProfile.objects.update_or_create(
                user=user,
                defaults={
                    'student_number': f'UNILUS{i:04d}',
                    'programme': 'BSc Information Technology',
                },
            )
            students.append(user)

        course1, _ = Course.objects.update_or_create(
            code='BIT326',
            year_of_study=3,
            semester=2,
            defaults={
                'name': 'Software Engineering',
                'programme': 'BIT',
                'lecturer': lecturers[0],
                'is_active': True,
            },
        )
        course2, _ = Course.objects.update_or_create(
            code='BIT340',
            year_of_study=3,
            semester=2,
            defaults={
                'name': 'Database Systems',
                'programme': 'BIT',
                'lecturer': lecturers[1],
                'is_active': True,
            },
        )

        for course, cohort in ((course1, students[:6]), (course2, students[6:])):
            for student in cohort:
                CourseEnrolment.objects.get_or_create(course=course, student=student)

        now = timezone.now()
        assignment1, _ = Assignment.objects.update_or_create(
            course=course1,
            title='Collaborative Project Management Prototype',
            defaults={
                'description': 'Design and demonstrate a group assignment workflow for UNILUS.',
                'deadline': now + timedelta(days=14),
                'status': Assignment.STATUS_PUBLISHED,
                'peer_evaluation_enabled': True,
                'max_group_size': 3,
            },
        )
        assignment2, _ = Assignment.objects.update_or_create(
            course=course2,
            title='Relational Schema Case Study',
            defaults={
                'description': 'Produce a normalised schema and short report as a group.',
                'deadline': now + timedelta(days=21),
                'status': Assignment.STATUS_PUBLISHED,
                'peer_evaluation_enabled': False,
                'max_group_size': 3,
            },
        )

        groups_spec = [
            (assignment1, 'Alpha', students[0:3]),
            (assignment1, 'Beta', students[3:6]),
            (assignment2, 'Gamma', students[6:9]),
            (assignment2, 'Delta', students[9:12]),
        ]
        groups = []
        for assignment, name, members in groups_spec:
            group, _ = Group.objects.update_or_create(
                assignment=assignment,
                name=name,
                defaults={'description': f'Demo group {name}', 'status': Group.STATUS_ACTIVE},
            )
            for member in members:
                GroupMembership.objects.get_or_create(
                    group=group,
                    student=member,
                    defaults={'status': GroupMembership.STATUS_APPROVED},
                )
            if group.leader_id != members[0].pk:
                group.leader = members[0]
                group.save(update_fields=['leader'])
            groups.append((group, members))

        # Tasks with mixed statuses (including overdue)
        alpha, alpha_members = groups[0]
        requirements_task, _ = Task.objects.update_or_create(
            group=alpha,
            title='Requirements draft',
            defaults={
                'description': 'Capture functional requirements.',
                'assigned_to': alpha_members[0],
                'created_by': alpha_members[0],
                'deadline': now - timedelta(days=1),
                'status': Task.STATUS_IN_PROGRESS,
            },
        )
        wireframes_task, _ = Task.objects.update_or_create(
            group=alpha,
            title='UI wireframes',
            defaults={
                'description': 'Prepare Bootstrap wireframes.',
                'assigned_to': alpha_members[1],
                'created_by': alpha_members[0],
                'deadline': now + timedelta(days=3),
                'status': Task.STATUS_TODO,
            },
        )
        demo_script_task, _ = Task.objects.update_or_create(
            group=alpha,
            title='Demo script',
            defaults={
                'description': 'Write lecturer demonstration script.',
                'assigned_to': alpha_members[2],
                'created_by': alpha_members[1],
                'deadline': now + timedelta(days=5),
                'status': Task.STATUS_COMPLETED,
                'completed_at': now - timedelta(hours=5),
            },
        )
        beta, beta_members = groups[1]
        beta_task, _ = Task.objects.update_or_create(
            group=beta,
            title='Database design review',
            defaults={
                'description': 'Review the schema and document key relationships.',
                'assigned_to': beta_members[1],
                'created_by': beta_members[0],
                'deadline': now + timedelta(days=4),
                'status': Task.STATUS_IN_PROGRESS,
            },
        )

        GroupMessage.objects.get_or_create(
            group=alpha,
            author=alpha_members[0],
            body='Please upload your section of the report before Friday.',
        )
        GroupMessage.objects.get_or_create(
            group=alpha,
            author=alpha_members[1],
            body='Wireframes are ready for review in the shared documents.',
        )
        ActivityLog.objects.get_or_create(
            user=alpha_members[0],
            event_type='seed',
            description='Demo activity: requirements discussion recorded.',
            assignment=assignment1,
            group=alpha,
        )

        demo_document = FileDocument.objects.filter(
            group=alpha,
            original_filename='requirements_outline.txt',
        ).first()
        if demo_document is None:
            demo_document = FileDocument(
                group=alpha,
                task=requirements_task,
                uploaded_by=alpha_members[0],
                original_filename='requirements_outline.txt',
                file_type='text/plain',
                file_size=0,
                version=1,
                document_type=FileDocument.TYPE_GROUP_FILE,
            )
            demo_document.file.save(
                'requirements_outline.txt',
                ContentFile(
                    b'DEMONSTRATION DOCUMENT - fictional project data.\n'
                    b'Functional requirements outline for the UNILUS SGAMS group assignment.\n'
                ),
                save=True,
            )
            demo_document.file_size = demo_document.file.size
            demo_document.save(update_fields=['file_size'])

        contribution_examples = (
            (
                alpha_members[0],
                'task_created',
                'Demo activity: created the requirements draft task.',
                requirements_task,
                None,
            ),
            (
                alpha_members[1],
                'task_updated',
                'Demo activity: updated the UI wireframes task.',
                wireframes_task,
                None,
            ),
            (
                alpha_members[2],
                'task_completed',
                'Demo activity: completed the demonstration script task.',
                demo_script_task,
                None,
            ),
            (
                alpha_members[0],
                'document_uploaded',
                'Demo activity: uploaded the requirements outline.',
                requirements_task,
                demo_document,
            ),
        )
        for student, event_type, description, task, document in contribution_examples:
            contribution = ContributionActivity.objects.filter(
                student=student,
                event_type=event_type,
                description=description,
                group=alpha,
                task=task,
                document=document,
            ).first()
            if contribution is None:
                ContributionActivity.objects.create(
                    student=student,
                    performed_by=student,
                    event_type=event_type,
                    description=description,
                    assignment=assignment1,
                    group=alpha,
                    task=task,
                    document=document,
                )

        # Submitted assignment for Alpha; unsubmitted for Beta
        submission, created = Submission.objects.get_or_create(
            group=alpha,
            is_latest=True,
            defaults={
                'submitted_by': alpha_members[0],
                'status': Submission.STATUS_SUBMITTED,
                'original_filename': 'alpha_final.txt',
                'notes': 'Demo final submission',
            },
        )
        if created or not submission.file:
            submission.file.save('alpha_final.txt', ContentFile(b'UNILUS Alpha demo submission'), save=True)

        grade, _ = Grade.objects.update_or_create(
            submission=submission,
            defaults={
                'graded_by': lecturers[0],
                'score': Decimal('78.00'),
                'max_score': Decimal('100.00'),
                'feedback': 'Solid collaboration evidence in the system. Continue refining documentation.',
                'is_released': True,
                'released_at': now,
            },
        )

        PeerReview.objects.update_or_create(
            assignment=assignment1,
            evaluator=alpha_members[0],
            evaluated=alpha_members[1],
            defaults={
                'score': 8,
                'criteria_scores': {
                    criterion: 8
                    for criterion in assignment1.peer_evaluation_criteria
                },
                'comment': 'Reliable teammate on wireframes.',
            },
        )

        for student in alpha_members:
            Notification.objects.get_or_create(
                user=student,
                message=f'Demo: welcome to group {alpha.name} for {assignment1.title}.',
                defaults={
                    'notification_type': Notification.TYPE_MEMBERSHIP,
                    'assignment': assignment1,
                    'group': alpha,
                },
            )
        for student in (alpha_members[0], beta_members[1]):
            task = requirements_task if student == alpha_members[0] else beta_task
            Notification.objects.get_or_create(
                user=student,
                message=f'Demo: task assigned — {task.title}.',
                defaults={
                    'notification_type': Notification.TYPE_TASK,
                    'assignment': task.group.assignment,
                    'group': task.group,
                    'task': task,
                },
            )
        for lecturer in lecturers:
            Notification.objects.get_or_create(
                user=lecturer,
                message=f'Demo: group progress is available for {assignment1.title}.',
                defaults={
                    'notification_type': Notification.TYPE_GENERAL,
                    'assignment': assignment1,
                    'group': alpha,
                },
            )

        self.stdout.write(self.style.SUCCESS('Demo data ready.'))
        self.stdout.write('Accounts (fictional):')
        self.stdout.write('  admin.demo / DemoAdmin123!')
        self.stdout.write('  lecturer.banda / DemoLecturer123!')
        self.stdout.write('  lecturer.phiri / DemoLecturer123!')
        self.stdout.write('  student01..student12 / DemoStudent123!')
        self.stdout.write(f'  Grade record id={grade.pk} released for Alpha.')
