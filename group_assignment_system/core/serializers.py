from django.core.exceptions import ValidationError as DjangoValidationError
from django.contrib.auth.password_validation import validate_password
from django.db.models import Q
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
    role = serializers.SerializerMethodField()
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

    def get_role(self, user):
        return User.ROLE_ADMIN if user.is_superuser else user.role

    def get_programme(self, user):
        return user.programme


class AdminUserSerializer(serializers.ModelSerializer):
    student_number = serializers.SerializerMethodField()
    programme = serializers.SerializerMethodField()
    password = serializers.CharField(
        write_only=True,
        required=False,
        trim_whitespace=False,
    )

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
            'password',
        )
        read_only_fields = ('id', 'student_number', 'programme')

    def get_student_number(self, user):
        return user.student_number

    def get_programme(self, user):
        return user.programme

    def validate(self, attrs):
        request = self.context['request']
        if self.instance is None and not attrs.get('password'):
            raise serializers.ValidationError(
                {'password': 'A password is required when creating a user.'}
            )

        password = attrs.get('password')
        if password:
            candidate = self.instance or User(**{
                key: value
                for key, value in attrs.items()
                if key != 'password'
            })
            try:
                validate_password(password, user=candidate)
            except DjangoValidationError as error:
                raise serializers.ValidationError({'password': error.messages}) from error

        if self.instance:
            if self.instance.pk == request.user.pk and (
                attrs.get('role', self.instance.role) != self.instance.role
                or not attrs.get('is_active', self.instance.is_active)
            ):
                raise serializers.ValidationError(
                    'You cannot change your own administrator role or deactivate your account.'
                )

            currently_administrator = (
                self.instance.is_active
                and (self.instance.is_admin_role() or self.instance.is_superuser)
            )
            will_be_administrator = (
                attrs.get('role', self.instance.role) == User.ROLE_ADMIN
                or self.instance.is_superuser
            )
            will_be_active = attrs.get('is_active', self.instance.is_active)
            if currently_administrator and not (
                will_be_administrator and will_be_active
            ):
                another_active_administrator = User.objects.filter(
                    is_active=True,
                ).filter(
                    Q(role=User.ROLE_ADMIN) | Q(is_superuser=True)
                ).exclude(pk=self.instance.pk).exists()
                if not another_active_administrator:
                    raise serializers.ValidationError(
                        'At least one active administrator must remain.'
                    )

        return attrs

    def create(self, validated_data):
        password = validated_data.pop('password')
        return User.objects.create_user(password=password, **validated_data)

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        if password:
            instance.set_password(password)
        return super().update(instance, validated_data)


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
    members = serializers.SerializerMethodField()
    progress = serializers.SerializerMethodField()
    assignment_title = serializers.CharField(source='assignment.title', read_only=True)
    course_id = serializers.IntegerField(source='assignment.course_id', read_only=True)
    course_code = serializers.CharField(source='assignment.course.code', read_only=True)
    course_name = serializers.CharField(source='assignment.course.name', read_only=True)
    assignment_deadline = serializers.DateTimeField(source='assignment.deadline', read_only=True)

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
            'members',
            'progress',
            'assignment_title',
            'course_id',
            'course_code',
            'course_name',
            'assignment_deadline',
        )
        read_only_fields = (
            'leader',
            'formation_type',
            'status',
            'created_at',
            'members',
            'progress',
            'assignment_title',
            'course_id',
            'course_code',
            'course_name',
            'assignment_deadline',
        )

    def get_members(self, group):
        return [
            {
                'id': membership.student_id,
                'username': membership.student.username,
                'full_name': (
                    membership.student.get_full_name()
                    or membership.student.username
                ),
                'is_leader': membership.student_id == group.leader_id,
            }
            for membership in group.memberships.all()
            if membership.status == GroupMembership.STATUS_APPROVED
        ]

    def get_progress(self, group):
        tasks = list(group.tasks.all())
        if not tasks:
            return 0
        completed = sum(task.status == Task.STATUS_COMPLETED for task in tasks)
        return int((completed / len(tasks)) * 100)

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
    assignment = serializers.IntegerField(source='group.assignment_id', read_only=True)
    assignment_title = serializers.CharField(source='group.assignment.title', read_only=True)
    course_id = serializers.IntegerField(source='group.assignment.course_id', read_only=True)
    course_name = serializers.CharField(source='group.assignment.course.name', read_only=True)
    group_name = serializers.CharField(source='group.name', read_only=True)
    assigned_to_name = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    effective_status = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = (
            'id',
            'assignment',
            'assignment_title',
            'course_id',
            'course_name',
            'group',
            'group_name',
            'title',
            'description',
            'priority',
            'assigned_to',
            'assigned_to_name',
            'created_by',
            'created_by_name',
            'deadline',
            'status',
            'effective_status',
            'created_at',
            'updated_at',
            'completed_at',
        )
        read_only_fields = (
            'assignment',
            'assignment_title',
            'course_id',
            'course_name',
            'group_name',
            'assigned_to_name',
            'created_by_name',
            'effective_status',
            'created_by',
            'created_at',
            'updated_at',
            'completed_at',
        )

    def get_assigned_to_name(self, task):
        return task.assigned_to.get_full_name() or task.assigned_to.username

    def get_created_by_name(self, task):
        if not task.created_by_id:
            return ''
        return task.created_by.get_full_name() or task.created_by.username

    def get_effective_status(self, task):
        return task.effective_status

    def validate(self, attrs):
        user = self.context['request'].user
        group = attrs.get('group', getattr(self.instance, 'group', None))
        assignee = attrs.get('assigned_to', getattr(self.instance, 'assigned_to', None))
        if self.instance and group != self.instance.group:
            raise serializers.ValidationError(
                {'group': 'A task cannot be moved to another group.'}
            )
        if group and group.status not in (Group.STATUS_ACTIVE, Group.STATUS_CLOSED):
            raise serializers.ValidationError(
                {'group': 'Tasks can only be created in active or closed groups.'}
            )
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
            student__role=User.ROLE_STUDENT,
            student__is_active=True,
        ).exists():
            raise serializers.ValidationError(
                {'assigned_to': 'The assignee must be an active student and approved member of this group.'}
            )
        return attrs


class TaskUpdateSerializer(serializers.ModelSerializer):
    author_name = serializers.SerializerMethodField()
    task_title = serializers.CharField(source='task.title', read_only=True)

    class Meta:
        model = TaskUpdate
        fields = ('id', 'task', 'task_title', 'author', 'author_name', 'update_text', 'timestamp')
        read_only_fields = ('task_title', 'author', 'author_name', 'timestamp')

    def get_author_name(self, update):
        if not update.author_id:
            return ''
        return update.author.get_full_name() or update.author.username

    def validate_task(self, task):
        user = self.context['request'].user
        if task.group.status not in (Group.STATUS_ACTIVE, Group.STATUS_CLOSED):
            raise serializers.ValidationError('Task updates require an active or closed group.')
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
