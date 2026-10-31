from django.contrib.admin import AdminSite
from django.contrib.admin.forms import AdminAuthenticationForm
from django.core.exceptions import ValidationError

from .permissions import is_system_administrator


class UNILUSAdminAuthenticationForm(AdminAuthenticationForm):
    def confirm_login_allowed(self, user):
        if not is_system_administrator(user):
            raise ValidationError(
                self.error_messages['invalid_login'],
                code='invalid_login',
            )


class UNILUSAdminSite(AdminSite):
    login_form = UNILUSAdminAuthenticationForm

    def has_permission(self, request):
        return bool(
            request.user.is_active
            and is_system_administrator(request.user)
        )
