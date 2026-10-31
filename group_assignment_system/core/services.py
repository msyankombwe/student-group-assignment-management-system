from datetime import timedelta

from django.utils import timezone

from .models import (
    ActivityLog,
    AuditLog,
    ContributionActivity,
    Group,
    GroupMembership,
    Notification,
    User,
)


def log_audit(user, action):
    if user is None or not user.is_authenticated:
        return None
    return AuditLog.objects.create(user=user, action=action)


def notify(
    user,
    message,
    notification_type=Notification.TYPE_GENERAL,
    assignment=None,
    group=None,
    task=None,
    deduplication_key=None,
):
    if user is None:
        return None
    if task is not None:
        group = group or task.group
        assignment = assignment or task.group.assignment
    if group is not None:
        assignment = assignment or group.assignment
    if deduplication_key:
        notification, created = Notification.objects.get_or_create(
            deduplication_key=deduplication_key,
            defaults={
                'user': user,
                'message': message,
                'notification_type': notification_type,
                'assignment': assignment,
                'group': group,
                'task': task,
            },
        )
        return notification if created else None
    recent = Notification.objects.filter(
        user=user,
        message=message,
        created_at__gte=timezone.now() - timedelta(minutes=5),
    ).exists()
    if recent:
        return None
    return Notification.objects.create(
        user=user,
        message=message,
        notification_type=notification_type,
        assignment=assignment,
        group=group,
        task=task,
    )


def record_activity(
    user,
    event_type,
    description,
    assignment=None,
    group=None,
    task=None,
    document=None,
    submission=None,
    contributor=None,
):
    if user is None or not user.is_authenticated:
        return None
    activity = ActivityLog.objects.create(
        user=user,
        event_type=event_type,
        description=description,
        assignment=assignment,
        group=group,
        task=task,
    )
    contribution_student = contributor or (
        user if user.role == User.ROLE_STUDENT else None
    )
    if contribution_student:
        ContributionActivity.objects.create(
            student=contribution_student,
            performed_by=user,
            event_type=event_type,
            description=description,
            assignment=assignment,
            group=group,
            task=task,
            document=document,
            submission=submission,
        )
    return activity


def user_manages_course(user, course):
    if not user.is_authenticated:
        return False
    if user.is_admin_role() or user.is_superuser:
        return True
    if course.lecturer_id == user.id:
        return True
    return course.lecturer_links.filter(lecturer=user).exists()


def user_manages_assignment(user, assignment):
    return user_manages_course(user, assignment.course)


def user_is_group_member(user, group):
    return group.memberships.filter(
        student=user,
        status='approved',
        group__status__in=(Group.STATUS_ACTIVE, Group.STATUS_CLOSED),
    ).exists()


def user_can_access_group(user, group):
    if not user.is_authenticated:
        return False
    if user.is_admin_role() or user.is_superuser:
        return True
    if user_manages_assignment(user, group.assignment):
        return True
    if user_is_group_member(user, group):
        return True
    return (
        group.status == Group.STATUS_PENDING
        and group.memberships.filter(
            student=user,
            status=GroupMembership.STATUS_PENDING,
        ).exists()
    ) or group.memberships.filter(
        student=user,
        status=GroupMembership.STATUS_INVITED,
    ).exists()


def user_can_access_group_content(user, group):
    if not user.is_authenticated:
        return False
    return (
        user.is_admin_role()
        or user.is_superuser
        or user_manages_assignment(user, group.assignment)
        or user_is_group_member(user, group)
    )


def can_manage_academic(user):
    return user.is_authenticated and (
        user.is_lecturer() or user.is_admin_role() or user.is_superuser
    )
