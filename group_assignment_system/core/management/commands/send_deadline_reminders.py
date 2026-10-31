from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import (
    Assignment,
    GroupMembership,
    Notification,
    Task,
    User,
)
from core.services import notify


class Command(BaseCommand):
    help = 'Create deduplicated publication and deadline notifications.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--hours-before',
            default='24,1',
            help='Comma-separated reminder lead times for task and assignment deadlines.',
        )
        parser.add_argument(
            '--window-minutes',
            type=int,
            default=60,
            help='How long after each lead-time point the reminder remains eligible.',
        )

    def handle(self, *args, **options):
        try:
            lead_hours = sorted({
                int(value.strip())
                for value in options['hours_before'].split(',')
                if value.strip()
            }, reverse=True)
        except ValueError as error:
            raise CommandError('--hours-before must contain comma-separated whole hours.') from error
        if any(hours <= 0 for hours in lead_hours):
            raise CommandError('--hours-before values must be greater than zero.')
        window_minutes = options['window_minutes']
        if window_minutes <= 0:
            raise CommandError('--window-minutes must be greater than zero.')

        now = timezone.now()
        created_count = 0
        for hours in lead_hours:
            window_end = now + timedelta(hours=hours)
            window_start = window_end - timedelta(minutes=window_minutes)
            created_count += self._remind_tasks(window_start, window_end, hours)
            created_count += self._remind_assignments(window_start, window_end, hours)

        created_count += self._notify_published_assignments(now)
        created_count += self._notify_overdue_tasks(now)
        self.stdout.write(self.style.SUCCESS(
            f'Created {created_count} deadline notification(s).'
        ))

    def _remind_tasks(self, window_start, window_end, lead_hours):
        created = 0
        tasks = Task.objects.filter(
            internal_deadline__due_at__gt=window_start,
            internal_deadline__due_at__lte=window_end,
        ).exclude(status=Task.STATUS_COMPLETED).select_related(
            'internal_deadline', 'group__assignment', 'assigned_to',
        )
        for task in tasks:
            due_at = task.internal_deadline.due_at.isoformat()
            key = f'task-deadline:{task.pk}:{due_at}:before:{lead_hours}:user:{task.assigned_to_id}'
            notification = notify(
                task.assigned_to,
                f'Task deadline approaching: {task.title or task.description[:80]} '
                f'is due {task.internal_deadline.due_at:%Y-%m-%d %H:%M}.',
                Notification.TYPE_DEADLINE,
                task=task,
                deduplication_key=key,
            )
            created += bool(notification)
        return created

    def _remind_assignments(self, window_start, window_end, lead_hours):
        created = 0
        assignments = Assignment.objects.filter(
            status=Assignment.STATUS_PUBLISHED,
            published_at__lte=timezone.now(),
            deadline__gt=window_start,
            deadline__lte=window_end,
            course__is_active=True,
        ).select_related('course')
        for assignment in assignments:
            student_ids = set(assignment.course.enrolments.values_list(
                'student_id', flat=True
            ))
            student_ids.update(assignment.group_memberships.filter(
                status=GroupMembership.STATUS_APPROVED,
            ).values_list('student_id', flat=True))
            due_at = assignment.deadline.isoformat()
            for student in User.objects.filter(pk__in=student_ids):
                key = (
                    f'assignment-deadline:{assignment.pk}:{due_at}:'
                    f'before:{lead_hours}:user:{student.pk}'
                )
                notification = notify(
                    student,
                    f'Assignment deadline approaching: {assignment.title} '
                    f'is due {assignment.deadline:%Y-%m-%d %H:%M}.',
                    Notification.TYPE_DEADLINE,
                    assignment=assignment,
                    deduplication_key=key,
                )
                created += bool(notification)
        return created

    def _notify_published_assignments(self, now):
        created = 0
        assignments = Assignment.objects.filter(
            status=Assignment.STATUS_PUBLISHED,
            published_at__lte=now,
            course__is_active=True,
        ).select_related('course').prefetch_related('course__enrolments__student')
        for assignment in assignments:
            for enrolment in assignment.course.enrolments.all():
                key = f'assignment-published:{assignment.pk}:user:{enrolment.student_id}'
                notification = notify(
                    enrolment.student,
                    f'New assignment published: {assignment.title}',
                    Notification.TYPE_ASSIGNMENT,
                    assignment=assignment,
                    deduplication_key=key,
                )
                created += bool(notification)
        return created

    def _notify_overdue_tasks(self, now):
        created = 0
        tasks = Task.objects.filter(
            internal_deadline__due_at__lt=now,
        ).exclude(status=Task.STATUS_COMPLETED).select_related(
            'internal_deadline',
            'group__assignment__course',
            'group__assignment__course__lecturer',
            'assigned_to',
        ).prefetch_related('group__assignment__course__lecturer_links__lecturer')
        for task in tasks:
            recipients = {task.assigned_to}
            course = task.group.assignment.course
            if course.lecturer_id:
                recipients.add(course.lecturer)
            recipients.update(
                lecturer_link.lecturer
                for lecturer_link in course.lecturer_links.all()
            )
            due_at = task.internal_deadline.due_at.isoformat()
            for recipient in recipients:
                key = f'task-overdue:{task.pk}:{due_at}:user:{recipient.pk}'
                notification = notify(
                    recipient,
                    f'Task overdue: {task.title or task.description[:80]} '
                    f'was due {task.internal_deadline.due_at:%Y-%m-%d %H:%M}.',
                    Notification.TYPE_DEADLINE,
                    task=task,
                    deduplication_key=key,
                )
                created += bool(notification)
        return created
