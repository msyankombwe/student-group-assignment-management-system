from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver
from django.utils import timezone

from .models import (
    AdministratorProfile,
    AssignmentClassRepresentative,
    GroupMembershipEvent,
    LecturerProfile,
    StudentProfile,
    User,
)


@receiver(post_save, sender=User)
def ensure_role_profile(sender, instance, **kwargs):
    if instance.is_superuser:
        AdministratorProfile.objects.get_or_create(user=instance)
    profile_models = {
        User.ROLE_STUDENT: StudentProfile,
        User.ROLE_LECTURER: LecturerProfile,
        User.ROLE_ADMIN: AdministratorProfile,
    }
    profile_model = profile_models.get(instance.role)
    if profile_model:
        profile_model.objects.get_or_create(user=instance)


@receiver(pre_delete, sender=User)
def close_representative_appointments_for_deleted_user(sender, instance, **kwargs):
    appointments = AssignmentClassRepresentative.objects.filter(
        student=instance,
        status=AssignmentClassRepresentative.STATUS_ACTIVE,
    ).select_related('student')
    for appointment in appointments:
        AssignmentClassRepresentative.objects.filter(pk=appointment.pk).update(
            status=AssignmentClassRepresentative.STATUS_REMOVED,
            ended_at=timezone.now(),
            end_reason='Automatically ended because the representative account was deleted.',
        )
        GroupMembershipEvent.objects.create(
            assignment=appointment.assignment,
            student=instance,
            actor=None,
            action=GroupMembershipEvent.ACTION_REPRESENTATIVE_REVOKED,
            representative_appointment=appointment,
            reason='Automatically ended because the representative account was deleted.',
        )
