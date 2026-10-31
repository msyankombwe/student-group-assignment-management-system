from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import AdministratorProfile, LecturerProfile, StudentProfile, User


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
