from django.utils import timezone
from rest_framework import serializers

from .models import (
    Assignment,
    Course,
    Group,
    GroupMembership,
    Task,
    TaskUpdate,
    User,
)
from .services import user_manages_course


class UserSerializer(serializers.ModelSerializer):
    student_number = serializers.SerializerMethodField()
    programme = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            'id',
            'username',
            'first_name',
            'last_name',
            'email',
            'role',
            'student_number',
            'programme',
            'is_active',
        )
        read_only_fields = fields

    def get_student_number(self, user):
        return user.student_number

    def get_programme(self, user):
        return user.programme


class CourseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Course
        fields = (
            'id',
            'code',
            'name',
            'programme',
            'semester',
            'academic_year',
            'lecturer',
            'is_active',
        )

    def validate_lecturer(self, lecturer):
        if lecturer and lecturer.role != User.ROLE_LECTURER:
            raise serializers.ValidationError('The assigned user must have the lecturer role.')
        return lecturer


class AssignmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Assignment
        fields = (
            'id',
            'course',
            'title',
            'description',
            'objectives',
            'moodle_reference',
            'published_at',
            'deadline',
            'status',
            'min_group_size',
            'max_group_size',
            'peer_evaluation_enabled',
        )

    def validate_course(self, course):
        user = self.context['request'].user
        if user.is_lecturer() and not user_manages_course(user, course):
            raise serializers.ValidationError('You can only manage assignments for your courses.')
        if (
            not course.is_active
            and (not self.instance or self.instance.course_id != course.pk)
        ):
            raise serializers.ValidationError('Assignments can only be created in active courses.')
        return course

    def validate(self, attrs):
        objectives = attrs.get(
            'objectives',
            getattr(self.instance, 'objectives', ''),
        )
        if not objectives or not objectives.strip():
            raise serializers.ValidationError(
                {'objectives': 'Assignment objectives are required.'}
            )
        deadline = attrs.get('deadline', getattr(self.instance, 'deadline', None))
        published_at = attrs.get(
            'published_at',
            getattr(self.instance, 'published_at', None),
        )
        status = attrs.get('status', getattr(self.instance, 'status', Assignment.STATUS_DRAFT))
        if deadline and status != Assignment.STATUS_CLOSED and deadline <= timezone.now():
            raise serializers.ValidationError(
                {'deadline': 'The submission deadline must be in the future.'}
            )
        if status == Assignment.STATUS_PUBLISHED and not published_at:
            raise serializers.ValidationError(
                {'published_at': 'Set a publication date before publishing.'}
            )
        if deadline and published_at and published_at >= deadline:
            raise serializers.ValidationError(
                {'published_at': 'Publication must be before the submission deadline.'}
            )
        min_group_size = attrs.get(
            'min_group_size',
            getattr(self.instance, 'min_group_size', 2),
        )
        max_group_size = attrs.get(
            'max_group_size',
            getattr(self.instance, 'max_group_size', 6),
        )
        if min_group_size > max_group_size:
            raise serializers.ValidationError(
                {'max_group_size': 'Maximum group size must not be smaller than the minimum.'}
            )
        return attrs


class GroupSerializer(serializers.ModelSerializer):
    class Meta:
        model = Group
        fields = (
            'id',
            'assignment',
            'name',
            'description',
            'max_members',
            'leader',
            'formation_type',
            'status',
            'created_at',
        )
        read_only_fields = ('leader', 'formation_type', 'status', 'created_at')

    def validate_assignment(self, assignment):
        user = self.context['request'].user
        if user.is_lecturer() and not user_manages_course(user, assignment.course):
            raise serializers.ValidationError('You can only manage groups for your courses.')
        return assignment

    def validate(self, attrs):
        group_max = attrs.get(
            'max_members',
            getattr(self.instance, 'max_members', None),
        )
        assignment = attrs.get(
            'assignment',
            getattr(self.instance, 'assignment', None),
        )
        if group_max is not None and assignment and not (
            assignment.min_group_size <= group_max <= assignment.max_group_size
        ):
            raise serializers.ValidationError(
                {'max_members': 'Group capacity must respect the assignment size limits.'}
            )
        if (
            self.instance
            and group_max is not None
            and group_max < self.instance.reserved_member_count()
        ):
            raise serializers.ValidationError(
                {'max_members': 'Capacity cannot be lower than current members and invitations.'}
            )
        return attrs


class TaskSerializer(serializers.ModelSerializer):
    deadline = serializers.DateTimeField()

    class Meta:
        model = Task
        fields = (
            'id',
            'group',
            'title',
            'description',
            'priority',
            'assigned_to',
            'deadline',
            'status',
            'created_at',
            'completed_at',
        )

    def validate(self, attrs):
        user = self.context['request'].user
        group = attrs.get('group', getattr(self.instance, 'group', None))
        assignee = attrs.get('assigned_to', getattr(self.instance, 'assigned_to', None))
        if user.is_lecturer() and group and not user_manages_course(user, group.assignment.course):
            raise serializers.ValidationError('You can only manage tasks for your courses.')
        if user.is_student() and group and not GroupMembership.objects.filter(
            group=group,
            student=user,
            status=GroupMembership.STATUS_APPROVED,
            group__status__in=(Group.STATUS_ACTIVE, Group.STATUS_CLOSED),
        ).exists():
            raise serializers.ValidationError(
                {'group': 'You must be an approved member of this group.'}
            )
        if group and assignee and not GroupMembership.objects.filter(
            group=group,
            student=assignee,
            status=GroupMembership.STATUS_APPROVED,
        ).exists():
            raise serializers.ValidationError(
                {'assigned_to': 'The assignee must be an approved member of this group.'}
            )
        return attrs


class TaskUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskUpdate
        fields = ('id', 'task', 'author', 'update_text', 'timestamp')
        read_only_fields = ('author', 'timestamp')

    def validate_task(self, task):
        user = self.context['request'].user
        if user.is_lecturer() and not user_manages_course(user, task.group.assignment.course):
            raise serializers.ValidationError('You can only update tasks for your courses.')
        if user.is_student() and not GroupMembership.objects.filter(
            group=task.group,
            student=user,
            status=GroupMembership.STATUS_APPROVED,
            group__status__in=(Group.STATUS_ACTIVE, Group.STATUS_CLOSED),
        ).exists():
            raise serializers.ValidationError(
                'You must be an approved member of the task group.'
            )
        return task
