from django.contrib.auth.backends import BaseBackend
from rest_framework.permissions import BasePermission, SAFE_METHODS

from .models import Assignment, Course, Group, Task, TaskUpdate, User
from .services import (
    user_can_manage_assignment_groups,
    user_is_group_member,
    user_manages_assignment,
    user_manages_course,
)


def is_system_administrator(user):
    return (
        user.is_authenticated
        and user.is_active
        and (user.is_admin_role() or user.is_superuser)
    )


class ScopedRolePermission(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated or not user.is_active:
            return False
        if user.role not in {choice[0] for choice in User.ROLE_CHOICES} and not user.is_superuser:
            return False
        if request.method in SAFE_METHODS or is_system_administrator(user):
            return True
        return user.is_lecturer() and User.ROLE_LECTURER in getattr(
            view, 'write_roles', (User.ROLE_ADMIN, User.ROLE_LECTURER)
        )

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS or is_system_administrator(request.user):
            return True
        if not request.user.is_lecturer():
            return False
        if isinstance(obj, Course):
            return user_manages_course(request.user, obj)
        if isinstance(obj, Assignment):
            return user_manages_assignment(request.user, obj)
        if isinstance(obj, Group):
            return user_manages_assignment(request.user, obj.assignment)
        if isinstance(obj, Task):
            return user_manages_assignment(request.user, obj.group.assignment)
        if isinstance(obj, TaskUpdate):
            return user_manages_assignment(request.user, obj.task.group.assignment)
        return False


class GroupManagementPermission(ScopedRolePermission):
    def has_permission(self, request, view):
        if super().has_permission(request, view):
            return True
        user = request.user
        if (
            request.method != 'POST'
            or getattr(view, 'action', None) != 'create'
            or not user
            or not user.is_authenticated
            or not user.is_active
            or not user.is_student()
        ):
            return False
        assignment_id = request.data.get('assignment')
        if not assignment_id:
            return False
        try:
            assignment_id = int(assignment_id)
        except (TypeError, ValueError):
            return False
        assignment = Assignment.objects.filter(pk=assignment_id).first()
        return bool(
            assignment
            and user_can_manage_assignment_groups(user, assignment)
        )


class TaskManagementPermission(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated or not user.is_active:
            return False
        if user.role not in {choice[0] for choice in User.ROLE_CHOICES} and not user.is_superuser:
            return False
        if request.method in SAFE_METHODS or is_system_administrator(user):
            return True
        if user.is_lecturer():
            return True
        return (
            user.is_student()
            and getattr(view, 'action', None)
            in getattr(view, 'student_write_actions', ())
        )

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS or is_system_administrator(request.user):
            return True
        if isinstance(obj, TaskUpdate):
            task = obj.task
        elif isinstance(obj, Task):
            task = obj
        else:
            return False
        if request.user.is_lecturer():
            return user_manages_assignment(request.user, task.group.assignment)
        if request.user.is_student():
            return (
                getattr(view, 'action', None)
                in getattr(view, 'student_write_actions', ())
                and user_is_group_member(request.user, task.group)
            )
        return False


class SystemAdministratorPermission(BasePermission):
    def has_permission(self, request, view):
        return is_system_administrator(request.user)

    def has_object_permission(self, request, view, obj):
        return is_system_administrator(request.user)


class RoleAdministratorBackend(BaseBackend):
    """Grant Django admin model permissions only to active system administrators."""

    def authenticate(self, request, **credentials):
        return None

    def get_user(self, user_id):
        return None

    def has_perm(self, user_obj, perm, obj=None):
        return bool(
            user_obj
            and user_obj.is_active
            and is_system_administrator(user_obj)
        )

    def has_module_perms(self, user_obj, app_label):
        return bool(
            user_obj
            and user_obj.is_active
            and is_system_administrator(user_obj)
        )
