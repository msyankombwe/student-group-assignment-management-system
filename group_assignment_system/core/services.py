from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import (
    ActivityLog,
    Assignment,
    AssignmentClassRepresentative,
    AuditLog,
    ContributionActivity,
    CourseEnrolment,
    Group,
    GroupMembership,
    GroupMembershipEvent,
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
    if not user.is_authenticated:
        return False
    if user.is_admin_role() or user.is_superuser:
        return True
    return user.is_lecturer() and user_manages_course(user, assignment.course)


def user_is_assignment_representative(user, assignment):
    return bool(
        user.is_authenticated
        and user.is_active
        and user.is_student()
        and assignment.assignment_type == Assignment.TYPE_GROUP
        and AssignmentClassRepresentative.objects.filter(
            assignment=assignment,
            student=user,
            status=AssignmentClassRepresentative.STATUS_ACTIVE,
        ).exists()
        and CourseEnrolment.objects.filter(
            course_id=assignment.course_id,
            student=user,
        ).exists()
    )


def user_can_appoint_assignment_representative(user, assignment):
    return bool(
        user.is_authenticated
        and user.is_active
        and (user.is_admin_role() or user.is_superuser or user.is_lecturer())
        and assignment.assignment_type == Assignment.TYPE_GROUP
        and user_manages_assignment(user, assignment)
    )


@transaction.atomic
def appoint_assignment_class_representative(actor, assignment, student):
    locked_assignment = Assignment.objects.select_for_update().get(pk=assignment.pk)
    if not user_can_appoint_assignment_representative(actor, locked_assignment):
        raise ValidationError(
            'Only an administrator or an authorized lecturer can appoint a representative.'
        )

    active_appointment = AssignmentClassRepresentative.objects.select_for_update().filter(
        assignment=locked_assignment,
        status=AssignmentClassRepresentative.STATUS_ACTIVE,
    ).first()
    if active_appointment:
        active_appointment.status = AssignmentClassRepresentative.STATUS_REPLACED
        active_appointment.ended_at = timezone.now()
        active_appointment.ended_by = actor
        active_appointment.save()

    return AssignmentClassRepresentative.objects.create(
        assignment=locked_assignment,
        student=student,
        appointed_by=actor,
    )


def user_can_manage_assignment_groups(user, assignment):
    if not user.is_authenticated or not user.is_active:
        return False
    if assignment.assignment_type != Assignment.TYPE_GROUP:
        return False
    return user_manages_assignment(user, assignment) or user_is_assignment_representative(
        user,
        assignment,
    )


@transaction.atomic
def create_assignment_group(actor, assignment, name, description='', max_members=None, students=()):
    assignment = Assignment.objects.select_for_update().select_related('course').get(
        pk=assignment.pk,
    )
    if not user_can_manage_assignment_groups(actor, assignment):
        raise ValidationError(
            'You are not authorized to create groups for this assignment.'
        )
    if not assignment.course.is_active:
        raise ValidationError('Groups cannot be created for an inactive course.')
    if assignment.status == Assignment.STATUS_CLOSED:
        raise ValidationError('Groups cannot be created for a closed assignment.')

    students = list(students)
    student_ids = [student.pk for student in students]
    if len(student_ids) != len(set(student_ids)):
        raise ValidationError('A student can only be selected once.')
    eligible_students = User.objects.filter(
        pk__in=student_ids,
        role=User.ROLE_STUDENT,
        is_active=True,
        course_enrolments__course_id=assignment.course_id,
    ).distinct()
    if eligible_students.count() != len(student_ids):
        raise ValidationError(
            'Every selected student must be active and enrolled in the assignment course.'
        )

    capacity = (
        assignment.max_group_size
        if max_members is None
        else max_members
    )
    if not assignment.min_group_size <= capacity <= assignment.max_group_size:
        raise ValidationError({
            'max_members': (
                'Group capacity must respect the assignment size limits '
                f'({assignment.min_group_size}–{assignment.max_group_size}).'
            )
        })
    if len(student_ids) > capacity:
        raise ValidationError({
            'students': 'The selected students exceed the maximum group capacity.'
        })
    existing_memberships = GroupMembership.objects.filter(
        assignment=assignment,
        student_id__in=student_ids,
    ).exclude(status=GroupMembership.STATUS_REJECTED)
    if existing_memberships.exists():
        raise ValidationError({
            'students': 'A selected student already has a group or request for this assignment.'
        })

    try:
        with transaction.atomic():
            group = Group.objects.create(
                assignment=assignment,
                name=name,
                description=description,
                max_members=capacity,
                created_by=actor,
                formation_type=Group.FORMATION_LECTURER,
                status=Group.STATUS_ACTIVE,
            )
            for student in students:
                membership = GroupMembership.objects.filter(
                    assignment=assignment,
                    student=student,
                    status=GroupMembership.STATUS_REJECTED,
                ).first()
                if membership:
                    membership.group = group
                    membership.status = GroupMembership.STATUS_APPROVED
                    membership.invited_by = None
                else:
                    membership = GroupMembership(
                        assignment=assignment,
                        group=group,
                        student=student,
                        status=GroupMembership.STATUS_APPROVED,
                    )
                membership.save()
                GroupMembershipEvent.objects.create(
                    assignment=assignment,
                    group=group,
                    student=student,
                    actor=actor,
                    action=GroupMembershipEvent.ACTION_ADDED,
                )
                GroupMembershipEvent.objects.create(
                    assignment=assignment,
                    group=group,
                    student=student,
                    actor=actor,
                    action=GroupMembershipEvent.ACTION_APPROVED,
                )
            return group
    except IntegrityError as error:
        raise ValidationError(
            'A selected student was allocated to another group at the same time. '
            'Refresh the page and try again.'
        ) from error


@transaction.atomic
def transfer_assignment_group_member(actor, membership_id, destination_group_id):
    try:
        membership_id = int(membership_id)
        destination_group_id = int(destination_group_id)
    except (TypeError, ValueError):
        raise ValidationError('Select a valid group membership and destination.')
    membership = GroupMembership.objects.filter(pk=membership_id).first()
    if membership is None:
        raise ValidationError('The selected group membership no longer exists.')
    assignment = Assignment.objects.select_for_update().select_related('course').get(
        pk=membership.assignment_id,
    )
    if not user_can_manage_assignment_groups(actor, assignment):
        raise ValidationError(
            'You are not authorized to manage groups for this assignment.'
        )
    membership = GroupMembership.objects.select_for_update().filter(
        pk=membership_id,
        assignment=assignment,
    ).first()
    if membership is None:
        raise ValidationError('The membership changed while the transfer was starting.')

    locked_groups = {
        group.pk: group
        for group in Group.objects.select_for_update().filter(
            pk__in=(membership.group_id, destination_group_id),
        ).select_related('assignment').order_by('pk')
    }
    source_group = locked_groups.get(membership.group_id)
    destination_group = locked_groups.get(destination_group_id)
    if source_group is None or destination_group is None:
        raise ValidationError('The source or destination group no longer exists.')
    if (
        source_group.assignment_id != assignment.pk
        or destination_group.assignment_id != assignment.pk
        or destination_group.assignment.assignment_type != Assignment.TYPE_GROUP
    ):
        raise ValidationError(
            'A student can only be transferred between groups for the same assignment.'
        )
    if source_group.pk == destination_group.pk:
        raise ValidationError('Choose a different destination group.')
    if source_group.status != Group.STATUS_ACTIVE:
        raise ValidationError('Membership cannot be transferred from an inactive group.')
    if destination_group.status != Group.STATUS_ACTIVE:
        raise ValidationError('Students can only be transferred to an active group.')
    if not CourseEnrolment.objects.filter(
        course_id=assignment.course_id,
        student_id=membership.student_id,
    ).exists():
        raise ValidationError('The student must be enrolled in the assignment course.')
    if membership.status != GroupMembership.STATUS_APPROVED:
        raise ValidationError('Only approved group members can be transferred.')
    if destination_group.reserved_member_count() >= destination_group.capacity:
        raise ValidationError('The destination group has reached its maximum capacity.')
    if GroupMembership.objects.filter(
        assignment=assignment,
        student_id=membership.student_id,
    ).exclude(pk=membership.pk).exclude(
        status=GroupMembership.STATUS_REJECTED,
    ).exists():
        raise ValidationError('The student already has another membership for this assignment.')

    previous_group = source_group
    previous_leader_id = source_group.leader_id
    try:
        with transaction.atomic():
            membership.group = destination_group
            membership.assignment = assignment
            membership.status = GroupMembership.STATUS_APPROVED
            membership.save()
            GroupMembershipEvent.objects.create(
                assignment=assignment,
                group=destination_group,
                previous_group=previous_group,
                student=membership.student,
                actor=actor,
                action=GroupMembershipEvent.ACTION_TRANSFERRED,
            )
            if previous_leader_id == membership.student_id:
                next_leader = previous_group.memberships.filter(
                    status=GroupMembership.STATUS_APPROVED,
                ).exclude(pk=membership.pk).order_by('joined_at', 'pk').first()
                previous_group.leader = next_leader.student if next_leader else None
                previous_group.save()
            return membership
    except IntegrityError as error:
        raise ValidationError(
            'The transfer conflicted with another membership change. Refresh and retry.'
        ) from error


@transaction.atomic
def dissolve_assignment_group(actor, group_id):
    group = Group.objects.select_for_update().select_related(
        'assignment__course',
    ).filter(pk=group_id).first()
    if group is None:
        raise ValidationError('The selected group no longer exists.')
    if not user_can_manage_assignment_groups(actor, group.assignment):
        raise ValidationError(
            'You are not authorized to dissolve groups for this assignment.'
        )
    if not (actor.is_admin_role() or actor.is_superuser or user_manages_assignment(
        actor,
        group.assignment,
    )):
        raise ValidationError(
            'Only an administrator or authorized lecturer can dissolve a group.'
        )
    if group.status != Group.STATUS_ACTIVE:
        raise ValidationError('Only active groups can be dissolved.')

    memberships = list(
        GroupMembership.objects.select_for_update().filter(group=group).select_related(
            'student',
        )
    )
    with transaction.atomic():
        for membership in memberships:
            if membership.status != GroupMembership.STATUS_REJECTED:
                GroupMembershipEvent.objects.create(
                    assignment=group.assignment,
                    group=group,
                    student=membership.student,
                    actor=actor,
                    action=GroupMembershipEvent.ACTION_REMOVED,
                )
        GroupMembership.objects.filter(pk__in=[
            membership.pk for membership in memberships
        ]).delete()
        group.status = Group.STATUS_CLOSED
        group.leader = None
        group.save()
    return group


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
