import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone


def default_peer_evaluation_criteria():
    return [
        'participation',
        'task_completion',
        'communication',
        'cooperation',
        'deadlines',
        'overall_contribution',
    ]


class User(AbstractUser):
    ROLE_STUDENT = 'student'
    ROLE_LECTURER = 'lecturer'
    ROLE_ADMIN = 'admin'
    ROLE_CHOICES = [
        (ROLE_STUDENT, 'Student'),
        (ROLE_LECTURER, 'Lecturer'),
        (ROLE_ADMIN, 'Administrator'),
    ]
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_STUDENT)

    @property
    def student_number(self):
        profile = getattr(self, 'student_profile', None)
        return (profile.student_number or '') if profile else ''

    @property
    def programme(self):
        profile = getattr(self, 'student_profile', None)
        return profile.programme if profile else ''

    @property
    def phone(self):
        for relation in ('student_profile', 'lecturer_profile', 'administrator_profile'):
            profile = getattr(self, relation, None)
            if profile:
                return profile.phone
        return ''

    def is_student(self):
        return self.role == self.ROLE_STUDENT

    def is_lecturer(self):
        return self.role == self.ROLE_LECTURER

    def is_admin_role(self):
        return self.role == self.ROLE_ADMIN or self.is_superuser


class StudentProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='student_profile',
    )
    student_number = models.CharField(max_length=50, unique=True, null=True, blank=True)
    programme = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    year_of_study = models.PositiveSmallIntegerField(null=True, blank=True)

    def __str__(self):
        return self.student_number or self.user.username


class LecturerProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='lecturer_profile',
    )
    department = models.CharField(max_length=120, blank=True)
    title = models.CharField(max_length=80, blank=True)
    phone = models.CharField(max_length=30, blank=True)

    def __str__(self):
        return self.user.get_full_name() or self.user.username


class AdministratorProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='administrator_profile',
    )
    department = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=30, blank=True)

    def __str__(self):
        return self.user.get_full_name() or self.user.username


class AcademicSession(models.Model):
    name = models.CharField(max_length=50, unique=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    def clean(self):
        super().clean()
        if not self.start_date:
            raise ValidationError({'start_date': 'Enter the academic year start date.'})
        if not self.end_date:
            raise ValidationError({'end_date': 'Enter the academic year end date.'})
        if self.end_date < self.start_date:
            raise ValidationError({
                'end_date': 'The academic year must end after it starts.',
            })

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Semester(models.Model):
    name = models.CharField(max_length=50)
    academic_session = models.ForeignKey(
        AcademicSession,
        on_delete=models.CASCADE,
        related_name='semesters',
        null=True,
        blank=True,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ('name', 'academic_session')

    def clean(self):
        super().clean()
        if not self.academic_session_id:
            raise ValidationError({
                'academic_session': 'A semester must belong to an academic year.',
            })

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Course(models.Model):
    YEAR_OF_STUDY_CHOICES = [
        (1, 'Year 1'),
        (2, 'Year 2'),
        (3, 'Year 3'),
        (4, 'Year 4'),
    ]
    SEMESTER_CHOICES = [
        (1, 'Semester 1'),
        (2, 'Semester 2'),
    ]

    code = models.CharField(max_length=30, blank=True)
    name = models.CharField(max_length=100)
    programme = models.CharField(max_length=120, blank=True)
    year_of_study = models.PositiveSmallIntegerField(
        choices=YEAR_OF_STUDY_CHOICES,
        null=True,
        blank=True,
    )
    semester = models.PositiveSmallIntegerField(
        choices=SEMESTER_CHOICES,
        null=True,
        blank=True,
    )
    legacy_academic_year = models.CharField(max_length=20, blank=True)
    legacy_semester = models.CharField(max_length=20, blank=True)
    legacy_semester_record = models.ForeignKey(
        'Semester',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    lecturer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='taught_courses',
        null=True,
        blank=True,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [models.Index(fields=['code', 'year_of_study'])]
        constraints = [
            models.UniqueConstraint(
                fields=['code', 'year_of_study', 'semester'],
                condition=~models.Q(code=''),
                name='unique_course_code_year_semester',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(year_of_study__isnull=True, semester__isnull=True)
                    | (
                        models.Q(year_of_study__gte=1, year_of_study__lte=4)
                        & models.Q(semester__gte=1, semester__lte=2)
                    )
                ),
                name='valid_course_study_classification',
            ),
        ]

    def __str__(self):
        label = self.code or self.name
        if self.year_of_study and self.semester:
            return f'{label} (Year {self.year_of_study}, Semester {self.semester})'
        return label

    def clean(self):
        super().clean()
        if self.year_of_study is None and self.semester is None:
            if self._state.adding:
                raise ValidationError(
                    'Select both a year of study and a semester for every new course.'
                )
            return
        if self.year_of_study is None:
            raise ValidationError({'year_of_study': 'Select a year of study.'})
        if self.semester is None:
            raise ValidationError({'semester': 'Select a semester.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class CourseLecturer(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='lecturer_links')
    lecturer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='course_lecturer_links',
    )

    class Meta:
        unique_together = ('course', 'lecturer')


class CourseEnrolment(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='enrolments')
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='course_enrolments',
    )
    enrolled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('course', 'student')

    def clean(self):
        if self.student_id and self.student.role != User.ROLE_STUDENT:
            raise ValidationError('Only students can enrol in courses.')

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class Assignment(models.Model):
    TYPE_INDIVIDUAL = 'individual'
    TYPE_GROUP = 'group'
    TYPE_CHOICES = [
        (TYPE_INDIVIDUAL, 'Individual Assignment'),
        (TYPE_GROUP, 'Group Assignment'),
    ]
    STATUS_DRAFT = 'draft'
    STATUS_PUBLISHED = 'published'
    STATUS_CLOSED = 'closed'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_PUBLISHED, 'Published'),
        (STATUS_CLOSED, 'Closed'),
    ]
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='assignments')
    assignment_type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        default=TYPE_GROUP,
    )
    title = models.CharField(max_length=200)
    description = models.TextField()
    objectives = models.TextField(default='')
    moodle_reference = models.URLField(
        blank=True,
        help_text='Optional Moodle course or assignment reference; this does not synchronize with Moodle.',
    )
    deadline = models.DateTimeField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PUBLISHED)
    created_at = models.DateTimeField(default=timezone.now)
    published_at = models.DateTimeField(null=True, blank=True)
    min_group_size = models.PositiveIntegerField(default=2)
    max_group_size = models.PositiveIntegerField(default=6)
    allow_student_group_proposals = models.BooleanField(default=True)
    allow_late_submissions = models.BooleanField(default=True)
    peer_evaluation_enabled = models.BooleanField(default=False)
    PEER_EVALUATION_CRITERIA = (
        ('participation', 'Participation'),
        ('task_completion', 'Task completion'),
        ('communication', 'Communication'),
        ('cooperation', 'Cooperation'),
        ('deadlines', 'Meeting internal deadlines'),
        ('overall_contribution', 'Overall contribution'),
    )
    peer_evaluation_criteria = models.JSONField(
        default=default_peer_evaluation_criteria,
        blank=True,
        help_text='Configured criteria used when peer evaluation is enabled.',
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(min_group_size__lte=models.F('max_group_size')),
                name='assignment_min_group_size_lte_max',
            ),
        ]

    def __str__(self):
        return self.title

    def clean(self):
        if self.assignment_type not in dict(self.TYPE_CHOICES):
            raise ValidationError({'assignment_type': 'Select a valid assignment type.'})
        if self.pk:
            previous_type = type(self).objects.filter(pk=self.pk).values_list(
                'assignment_type',
                flat=True,
            ).first()
            if (
                previous_type
                and previous_type != self.assignment_type
                and (
                    self.groups.exists()
                    or self.submissions.exists()
                    or self.representative_appointments.exists()
                )
            ):
                raise ValidationError(
                    'Assignment type cannot change after groups or submissions exist.'
                )

    def is_published(self):
        return (
            self.status == self.STATUS_PUBLISHED
            and self.published_at is not None
            and self.published_at <= timezone.now()
        )

    def save(self, *args, **kwargs):
        if self.status == self.STATUS_PUBLISHED and self.published_at is None:
            self.published_at = timezone.now()
        self.clean()
        super().save(*args, **kwargs)


class AssignmentClassRepresentative(models.Model):
    STATUS_ACTIVE = 'active'
    STATUS_REPLACED = 'replaced'
    STATUS_REMOVED = 'removed'
    STATUS_CHOICES = [
        (STATUS_ACTIVE, 'Active'),
        (STATUS_REPLACED, 'Replaced'),
        (STATUS_REMOVED, 'Removed'),
    ]

    assignment = models.ForeignKey(
        Assignment,
        on_delete=models.CASCADE,
        related_name='representative_appointments',
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assignment_representative_appointments',
    )
    student_username_snapshot = models.CharField(max_length=150, blank=True)
    student_full_name_snapshot = models.CharField(max_length=255, blank=True)
    student_number_snapshot = models.CharField(max_length=50, blank=True)
    appointed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='representative_appointments_made',
    )
    appointed_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=12,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
    )
    ended_at = models.DateTimeField(null=True, blank=True)
    ended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='representative_appointments_ended',
    )
    appointed_by_username_snapshot = models.CharField(max_length=150, blank=True)
    appointed_by_full_name_snapshot = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-appointed_at']
        constraints = [
            models.UniqueConstraint(
                fields=['assignment'],
                condition=models.Q(status='active'),
                name='one_active_representative_per_assignment',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status='active')
                    | models.Q(student__isnull=False)
                ),
                name='active_representative_requires_student',
            ),
        ]

    def clean(self):
        if not self.assignment_id:
            raise ValidationError({'assignment': 'Select an assignment for this appointment.'})
        assignment = Assignment.objects.filter(pk=self.assignment_id).first()
        if assignment is None:
            raise ValidationError({'assignment': 'The selected assignment does not exist.'})
        if assignment.assignment_type != Assignment.TYPE_GROUP:
            raise ValidationError({
                'assignment': 'Representatives can only be appointed to group assignments.'
            })
        creating = self._state.adding
        if self.student_id:
            student = User.objects.filter(pk=self.student_id).first()
            if student is None:
                raise ValidationError({'student': 'The selected student does not exist.'})
            if (creating or self.status == self.STATUS_ACTIVE) and (
                student.role != User.ROLE_STUDENT or not student.is_active
            ):
                raise ValidationError({
                    'student': 'The representative must be an active student.'
                })
            if (creating or self.status == self.STATUS_ACTIVE) and not CourseEnrolment.objects.filter(
                course_id=assignment.course_id,
                student_id=self.student_id,
            ).exists():
                raise ValidationError({
                    'student': 'The representative must be enrolled in the assignment course.'
                })
        elif self.status == self.STATUS_ACTIVE:
            raise ValidationError({'student': 'An active appointment requires a student.'})

        if self.status not in dict(self.STATUS_CHOICES):
            raise ValidationError({'status': 'Select a valid appointment status.'})
        if not self.appointed_by_id:
            if creating:
                raise ValidationError({
                    'appointed_by': 'Record the lecturer or administrator making the appointment.'
                })
        elif creating:
            from .services import user_can_appoint_assignment_representative

            appointing_officer = User.objects.filter(pk=self.appointed_by_id).first()
            if appointing_officer is None or not user_can_appoint_assignment_representative(
                appointing_officer,
                assignment,
            ):
                raise ValidationError({
                    'appointed_by': (
                        'The appointing officer must be an administrator or a lecturer '
                        'authorized to manage this assignment.'
                    )
                })

        if not creating:
            existing = type(self).objects.filter(pk=self.pk).values(
                'assignment_id',
                'student_id',
                'appointed_by_id',
                'student_username_snapshot',
                'student_full_name_snapshot',
                'student_number_snapshot',
                'appointed_by_username_snapshot',
                'appointed_by_full_name_snapshot',
            ).first()
            if existing and any(
                getattr(self, field) != value
                for field, value in existing.items()
            ):
                raise ValidationError(
                    'Appointment identity and history fields cannot be changed.'
                )

        if self.status == self.STATUS_ACTIVE and self.ended_at is not None:
            raise ValidationError({
                'ended_at': 'An active appointment cannot have an end date.'
            })
        if self.status != self.STATUS_ACTIVE and self.ended_at is None:
            raise ValidationError({
                'ended_at': 'A closed appointment must have an end date.'
            })
        if self.ended_by_id:
            from .services import user_can_appoint_assignment_representative

            ending_officer = User.objects.filter(pk=self.ended_by_id).first()
            if ending_officer is None or not user_can_appoint_assignment_representative(
                ending_officer,
                assignment,
            ):
                raise ValidationError({
                    'ended_by': (
                        'Only an authorized lecturer or administrator can end an appointment.'
                    )
                })

    def save(self, *args, **kwargs):
        if self._state.adding:
            if self.student_id:
                self.student_username_snapshot = self.student.username
                self.student_full_name_snapshot = (
                    self.student.get_full_name() or self.student.username
                )
                self.student_number_snapshot = self.student.student_number
            if self.appointed_by_id:
                self.appointed_by_username_snapshot = self.appointed_by.username
                self.appointed_by_full_name_snapshot = (
                    self.appointed_by.get_full_name() or self.appointed_by.username
                )
        self.full_clean()
        super().save(*args, **kwargs)


class Group(models.Model):
    STATUS_ACTIVE = 'active'
    STATUS_PENDING = 'pending'
    STATUS_CLOSED = 'closed'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = [
        (STATUS_ACTIVE, 'Active'),
        (STATUS_PENDING, 'Pending approval'),
        (STATUS_CLOSED, 'Closed'),
        (STATUS_REJECTED, 'Rejected'),
    ]
    FORMATION_LECTURER = 'lecturer'
    FORMATION_STUDENT = 'student_proposed'
    FORMATION_CHOICES = [
        (FORMATION_LECTURER, 'Lecturer created'),
        (FORMATION_STUDENT, 'Student proposed'),
    ]
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name='groups')
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    max_members = models.PositiveSmallIntegerField(null=True, blank=True)
    leader = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='led_groups',
    )
    formation_type = models.CharField(
        max_length=20,
        choices=FORMATION_CHOICES,
        default=FORMATION_LECTURER,
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_groups',
    )

    def __str__(self):
        return self.name

    @property
    def capacity(self):
        return self.max_members or self.assignment.max_group_size

    def member_count(self):
        return self.memberships.filter(status=GroupMembership.STATUS_APPROVED).count()

    def reserved_member_count(self):
        return self.memberships.filter(
            status__in=(
                GroupMembership.STATUS_APPROVED,
                GroupMembership.STATUS_PENDING,
                GroupMembership.STATUS_INVITED,
            )
        ).count()

    def clean(self):
        if self.assignment_id and self.assignment.assignment_type != Assignment.TYPE_GROUP:
            raise ValidationError({
                'assignment': 'Groups can only be created for group assignments.'
            })
        if self.assignment_id and self.max_members is not None:
            if not self.assignment.min_group_size <= self.max_members <= self.assignment.max_group_size:
                raise ValidationError({
                    'max_members': (
                        'Group capacity must be within the assignment group-size limits '
                        f'({self.assignment.min_group_size}–{self.assignment.max_group_size}).'
                    )
                })
        if self.leader_id:
            if self.leader.role != User.ROLE_STUDENT:
                raise ValidationError({'leader': 'The group leader must be a student.'})
            if self.pk and not self.memberships.filter(
                student=self.leader,
                status__in=(
                    GroupMembership.STATUS_APPROVED,
                    GroupMembership.STATUS_PENDING,
                ),
            ).exists():
                raise ValidationError({'leader': 'The group leader must be a group member.'})

    def save(self, *args, **kwargs):
        if self.max_members is None and self.assignment_id:
            self.max_members = self.assignment.max_group_size
        self.full_clean()
        super().save(*args, **kwargs)

    def task_completion_percent(self):
        total = self.tasks.count()
        if total == 0:
            return 0
        completed = self.tasks.filter(status=Task.STATUS_COMPLETED).count()
        return int((completed / total) * 100)


class GroupMembership(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_INVITED = 'invited'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_REJECTED, 'Rejected'),
        (STATUS_INVITED, 'Invited'),
    ]
    assignment = models.ForeignKey(
        Assignment,
        on_delete=models.CASCADE,
        related_name='group_memberships',
    )
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='memberships')
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='group_memberships',
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sent_group_invitations',
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_APPROVED)
    joined_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['group', 'student'], name='unique_group_membership'),
            models.UniqueConstraint(
                fields=['assignment', 'student'],
                name='unique_student_assignment_group',
            ),
        ]

    def __str__(self):
        return f'{self.student} in {self.group}'

    def clean(self):
        if self.student_id and self.student.role != User.ROLE_STUDENT:
            raise ValidationError('Only students can join an assignment group.')
        if (
            self.group_id
            and self.group.assignment.assignment_type != Assignment.TYPE_GROUP
        ):
            raise ValidationError('Only group assignments can have group memberships.')
        if self.group_id and self.assignment_id != self.group.assignment_id:
            raise ValidationError('The membership assignment must match the group assignment.')
        if (
            self.group_id
            and self.student_id
            and not CourseEnrolment.objects.filter(
                course_id=self.group.assignment.course_id,
                student_id=self.student_id,
            ).exists()
        ):
            raise ValidationError('The student must be enrolled in the group assignment course.')
        if self.assignment_id and self.student_id:
            existing = GroupMembership.objects.filter(
                assignment_id=self.assignment_id,
                student_id=self.student_id,
            )
            if self.pk:
                existing = existing.exclude(pk=self.pk)
            if existing.exists():
                raise ValidationError('A student can only belong to one group per assignment.')
        if self.group_id and self.status in (
            self.STATUS_APPROVED,
            self.STATUS_PENDING,
            self.STATUS_INVITED,
        ):
            occupied = self.group.memberships.filter(
                status__in=(self.STATUS_APPROVED, self.STATUS_PENDING, self.STATUS_INVITED)
            )
            if self.pk:
                occupied = occupied.exclude(pk=self.pk)
            if occupied.count() >= self.group.capacity:
                raise ValidationError('This group has reached its maximum size.')
            if self.group.status == Group.STATUS_REJECTED:
                raise ValidationError('Membership cannot be added to a rejected group.')

    def save(self, *args, **kwargs):
        if self.group_id:
            self.assignment_id = self.group.assignment_id
        self.full_clean()
        super().save(*args, **kwargs)


class GroupMembershipEventQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('Group membership events are immutable.')

    def delete(self):
        raise ValidationError('Group membership events cannot be deleted.')

    def bulk_create(self, objs, **kwargs):
        raise ValidationError('Create group membership events individually for validation.')


class GroupMembershipEvent(models.Model):
    ACTION_ADDED = 'added'
    ACTION_APPROVED = 'approved'
    ACTION_REMOVED = 'removed'
    ACTION_TRANSFERRED = 'transferred'
    ACTION_REJECTED = 'rejected'
    ACTION_INVITED = 'invited'
    ACTION_CHOICES = [
        (ACTION_ADDED, 'Added'),
        (ACTION_APPROVED, 'Approved'),
        (ACTION_REMOVED, 'Removed'),
        (ACTION_TRANSFERRED, 'Transferred'),
        (ACTION_REJECTED, 'Rejected'),
        (ACTION_INVITED, 'Invited'),
    ]

    assignment = models.ForeignKey(
        Assignment,
        on_delete=models.SET_NULL,
        null=True,
        related_name='membership_events',
    )
    group = models.ForeignKey(
        Group,
        on_delete=models.SET_NULL,
        null=True,
        related_name='membership_events',
    )
    previous_group = models.ForeignKey(
        Group,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='membership_transfers_from',
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='group_membership_events',
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='group_membership_events_performed',
    )
    action = models.CharField(max_length=12, choices=ACTION_CHOICES)
    occurred_at = models.DateTimeField(auto_now_add=True)
    assignment_title_snapshot = models.CharField(max_length=200, blank=True)
    group_name_snapshot = models.CharField(max_length=100, blank=True)
    previous_group_name_snapshot = models.CharField(max_length=100, blank=True)
    student_username_snapshot = models.CharField(max_length=150, blank=True)
    student_full_name_snapshot = models.CharField(max_length=255, blank=True)
    student_number_snapshot = models.CharField(max_length=50, blank=True)
    actor_username_snapshot = models.CharField(max_length=150, blank=True)
    actor_full_name_snapshot = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-occurred_at']
        indexes = [
            models.Index(fields=['assignment', 'occurred_at']),
            models.Index(fields=['group', 'occurred_at']),
        ]

    objects = GroupMembershipEventQuerySet.as_manager()

    def clean(self):
        if self.group_id and not self.assignment_id:
            group = Group.objects.filter(pk=self.group_id).first()
            if group is None:
                raise ValidationError({'group': 'The selected group does not exist.'})
            self.assignment_id = group.assignment_id
        assignment = Assignment.objects.filter(pk=self.assignment_id).first()
        group = Group.objects.filter(pk=self.group_id).first()
        student = User.objects.filter(pk=self.student_id).first()
        actor = User.objects.filter(pk=self.actor_id).first()
        if assignment is None:
            raise ValidationError({'assignment': 'A valid assignment is required for an event.'})
        if group is None:
            raise ValidationError({'group': 'A valid group is required for an event.'})
        if student is None:
            raise ValidationError({'student': 'A valid student is required for an event.'})
        if actor is None:
            raise ValidationError({'actor': 'A valid actor is required for an event.'})
        if (
            self.assignment_id
            and self.group_id
            and group.assignment_id != self.assignment_id
        ):
            raise ValidationError('The event assignment must match its group.')
        if self.action not in dict(self.ACTION_CHOICES):
            raise ValidationError({'action': 'Select a valid membership action.'})
        if self.action == self.ACTION_TRANSFERRED:
            if not self.previous_group_id:
                raise ValidationError({
                    'previous_group': 'A transfer must record the previous group.'
                })
            if self.previous_group_id == self.group_id:
                raise ValidationError({
                    'group': 'The destination group must differ from the previous group.'
                })
            previous_group = Group.objects.filter(pk=self.previous_group_id).first()
            if previous_group is None or previous_group.assignment_id != self.assignment_id:
                raise ValidationError({
                    'previous_group': 'The previous group must belong to this assignment.'
                })
        elif self.previous_group_id:
            raise ValidationError({
                'previous_group': 'Only transfer events can record a previous group.'
            })
        if student.role != User.ROLE_STUDENT:
            raise ValidationError('Membership events must refer to a student.')
        if not CourseEnrolment.objects.filter(
            course_id=self.assignment.course_id,
            student_id=self.student_id,
        ).exists():
            raise ValidationError('Membership events must refer to an enrolled student.')

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Group membership events are immutable.')
        self.full_clean()
        self.assignment_title_snapshot = Assignment.objects.get(
            pk=self.assignment_id
        ).title
        self.group_name_snapshot = Group.objects.get(pk=self.group_id).name
        if self.previous_group_id:
            self.previous_group_name_snapshot = Group.objects.get(
                pk=self.previous_group_id
            ).name
        student = User.objects.get(pk=self.student_id)
        actor = User.objects.get(pk=self.actor_id)
        self.student_username_snapshot = student.username
        self.student_full_name_snapshot = (
            student.get_full_name() or student.username
        )
        self.student_number_snapshot = student.student_number
        self.actor_username_snapshot = actor.username
        self.actor_full_name_snapshot = actor.get_full_name() or actor.username
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Group membership events cannot be deleted.')


class Task(models.Model):
    STATUS_TODO = 'todo'
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_UNDER_REVIEW = 'under_review'
    STATUS_COMPLETED = 'completed'
    STATUS_OVERDUE = 'overdue'
    STATUS_CHOICES = [
        (STATUS_TODO, 'Not Started'),
        (STATUS_IN_PROGRESS, 'In Progress'),
        (STATUS_UNDER_REVIEW, 'Under Review'),
        (STATUS_COMPLETED, 'Completed'),
    ]
    PRIORITY_LOW = 'low'
    PRIORITY_NORMAL = 'normal'
    PRIORITY_HIGH = 'high'
    PRIORITY_URGENT = 'urgent'
    PRIORITY_CHOICES = [
        (PRIORITY_LOW, 'Low'),
        (PRIORITY_NORMAL, 'Normal'),
        (PRIORITY_HIGH, 'High'),
        (PRIORITY_URGENT, 'Urgent'),
    ]
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='tasks')
    assignees = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through='TaskAssignment',
        through_fields=('task', 'student'),
        related_name='assigned_group_tasks',
    )
    title = models.CharField(max_length=200, blank=True)
    description = models.TextField()
    priority = models.CharField(
        max_length=10,
        choices=PRIORITY_CHOICES,
        default=PRIORITY_NORMAL,
        db_index=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_tasks',
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='assigned_tasks',
    )
    created_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_TODO)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title or self.description[:60]

    def is_overdue(self):
        deadline = self.deadline
        return bool(
            deadline
            and self.status != self.STATUS_COMPLETED
            and deadline < timezone.now()
        )

    @property
    def effective_status(self):
        return self.STATUS_OVERDUE if self.is_overdue() else self.status

    @property
    def effective_status_display(self):
        if self.is_overdue():
            return 'Overdue'
        return self.get_status_display()

    @property
    def deadline(self):
        deadline_value = getattr(self, '_deadline_value', None)
        if deadline_value is not None:
            return deadline_value
        try:
            deadline = self.internal_deadline
        except InternalDeadline.DoesNotExist:
            return None
        return deadline.due_at if deadline else None

    @deadline.setter
    def deadline(self, value):
        self._deadline_value = value

    def mark_completed_if_needed(self):
        if self.status == self.STATUS_COMPLETED and self.completed_at is None:
            self.completed_at = timezone.now()
        elif self.status != self.STATUS_COMPLETED:
            self.completed_at = None

    @transaction.atomic
    def save(self, *args, **kwargs):
        self.mark_completed_if_needed()
        if kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = (
                set(kwargs['update_fields']) | {'completed_at', 'updated_at'}
            )
        previous_assignee_id = None
        if self.pk:
            previous_assignee_id = Task.objects.filter(pk=self.pk).values_list(
                'assigned_to_id',
                flat=True,
            ).first()
        deadline_value = self.deadline
        if deadline_value is None:
            raise ValidationError({'deadline': 'A task deadline is required.'})
        if self.assigned_to_id and self.group_id and not GroupMembership.objects.filter(
            group_id=self.group_id,
            student_id=self.assigned_to_id,
            status=GroupMembership.STATUS_APPROVED,
        ).exists():
            raise ValidationError('The assignee must be an approved member of this group.')
        super().save(*args, **kwargs)
        if deadline_value is not None:
            InternalDeadline.objects.update_or_create(
                task=self,
                defaults={'due_at': deadline_value, 'created_by_id': self.created_by_id},
            )
        if previous_assignee_id and previous_assignee_id != self.assigned_to_id:
            TaskAssignment.objects.filter(
                task=self,
                student_id=previous_assignee_id,
            ).delete()
        TaskAssignment.objects.get_or_create(
            task=self,
            student_id=self.assigned_to_id,
            defaults={'assigned_by_id': self.created_by_id},
        )
        if hasattr(self, '_deadline_value'):
            del self._deadline_value


class TaskAssignment(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='task_assignments')
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='task_assignment_records',
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='task_assignments_created',
    )
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['task', 'student'], name='unique_task_assignee'),
        ]
        indexes = [models.Index(fields=['student', 'assigned_at'])]

    def clean(self):
        if self.student_id and self.student.role != User.ROLE_STUDENT:
            raise ValidationError('Only students can be assigned to group tasks.')
        if self.task_id and self.student_id and not GroupMembership.objects.filter(
            group_id=self.task.group_id,
            student_id=self.student_id,
            status=GroupMembership.STATUS_APPROVED,
        ).exists():
            raise ValidationError('A task assignee must be an approved member of its group.')

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class InternalDeadline(models.Model):
    task = models.OneToOneField(Task, on_delete=models.CASCADE, related_name='internal_deadline')
    due_at = models.DateTimeField(db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='internal_deadlines_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.task}: {self.due_at}'


class TaskUpdate(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='updates')
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='task_updates',
    )
    update_text = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

    @transaction.atomic
    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        Task.objects.filter(pk=self.task_id).update(updated_at=timezone.now())


def group_document_upload_path(instance, filename):
    extension = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    suffix = f'.{extension}' if extension else ''
    return f'group_documents/{instance.group_id}/{uuid.uuid4().hex}{suffix}'


class FileDocument(models.Model):
    TYPE_GROUP_FILE = 'group_file'
    TYPE_CONTRIBUTION_EVIDENCE = 'contribution_evidence'
    TYPE_CHOICES = [
        (TYPE_GROUP_FILE, 'Group file'),
        (TYPE_CONTRIBUTION_EVIDENCE, 'Contribution evidence'),
    ]
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='files')
    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='evidence_items',
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='uploaded_files',
    )
    file = models.FileField(upload_to=group_document_upload_path)
    original_filename = models.CharField(max_length=255, blank=True)
    file_type = models.CharField(max_length=100, blank=True)
    file_size = models.PositiveIntegerField(default=0)
    version = models.PositiveIntegerField(default=1)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    document_type = models.CharField(max_length=30, choices=TYPE_CHOICES, default=TYPE_GROUP_FILE)

    class Meta:
        indexes = [
            models.Index(fields=['group', 'uploaded_at']),
            models.Index(fields=['task', 'uploaded_at']),
        ]
        constraints = [
            models.CheckConstraint(condition=models.Q(file_size__gte=0), name='file_document_size_nonnegative'),
        ]

    def clean(self):
        if self.task_id and self.group_id and self.task.group_id != self.group_id:
            raise ValidationError('The document task must belong to its group.')

    @property
    def assignment(self):
        return self.group.assignment

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.original_filename or self.file.name


class GroupMessage(models.Model):
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='messages')
    task = models.ForeignKey(
        Task,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='discussion_messages',
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='group_messages',
    )
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def clean(self):
        if self.task_id and self.group_id and self.task.group_id != self.group_id:
            raise ValidationError('A discussion task must belong to its group.')

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class Submission(models.Model):
    STATUS_SUBMITTED = 'submitted'
    STATUS_LATE = 'late'
    STATUS_RESUBMITTED = 'resubmitted'
    STATUS_UNDER_REVIEW = 'under_review'
    STATUS_FEEDBACK_PUBLISHED = 'feedback_published'
    STATUS_CHOICES = [
        (STATUS_SUBMITTED, 'Submitted'),
        (STATUS_LATE, 'Submitted Late'),
        (STATUS_RESUBMITTED, 'Resubmitted'),
        (STATUS_UNDER_REVIEW, 'Under Review'),
        (STATUS_FEEDBACK_PUBLISHED, 'Feedback Published'),
    ]
    assignment = models.ForeignKey(
        Assignment,
        on_delete=models.CASCADE,
        related_name='submissions',
    )
    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='submissions',
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='submissions_made',
    )
    file = models.FileField(upload_to='submissions/')
    original_filename = models.CharField(max_length=255, blank=True)
    moodle_reference = models.URLField(
        blank=True,
        help_text='Optional Moodle submission/receipt reference; this does not upload or synchronize work.',
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_SUBMITTED)
    is_latest = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-submitted_at']
        constraints = [
            models.UniqueConstraint(
                fields=['group'],
                condition=models.Q(is_latest=True, group__isnull=False),
                name='one_latest_submission_per_group',
            ),
            models.UniqueConstraint(
                fields=['assignment', 'submitted_by'],
                condition=models.Q(is_latest=True, group__isnull=True),
                name='one_latest_individual_submission',
            ),
        ]

    @property
    def is_late(self):
        return bool(
            self.submitted_at
            and self.submitted_at > self.assignment.deadline
        )

    def clean_fields(self, exclude=None):
        if self.group_id and not self.assignment_id:
            group = Group.objects.filter(pk=self.group_id).first()
            if group:
                self.assignment_id = group.assignment_id
        super().clean_fields(exclude=exclude)

    def clean(self):
        if self.group_id and not self.assignment_id:
            group = Group.objects.filter(pk=self.group_id).first()
            if group is None:
                raise ValidationError({'group': 'The selected group does not exist.'})
            self.assignment_id = group.assignment_id
        if not self.assignment_id:
            return
        if self.group_id:
            if self.group.assignment_id != self.assignment_id:
                raise ValidationError('The submission assignment must match its group.')
            if self.assignment.assignment_type != Assignment.TYPE_GROUP:
                raise ValidationError('Individual assignments do not accept group submissions.')
            if self.submitted_by_id and not GroupMembership.objects.filter(
                group_id=self.group_id,
                student_id=self.submitted_by_id,
                status=GroupMembership.STATUS_APPROVED,
            ).exists():
                raise ValidationError(
                    'Only an approved group member can submit on behalf of the group.'
                )
        else:
            if self.assignment.assignment_type != Assignment.TYPE_INDIVIDUAL:
                raise ValidationError('Group assignments require a group submission.')
            if self.submitted_by_id:
                if self.submitted_by.role != User.ROLE_STUDENT:
                    raise ValidationError('Only students can submit an individual assignment.')
                if not CourseEnrolment.objects.filter(
                    course_id=self.assignment.course_id,
                    student_id=self.submitted_by_id,
                ).exists():
                    raise ValidationError(
                        'The student must be enrolled in the assignment course.'
                    )

    def save(self, *args, **kwargs):
        if self.group_id:
            if self.assignment_id and self.assignment_id != self.group.assignment_id:
                raise ValidationError('The submission assignment must match its group.')
            self.assignment_id = self.group.assignment_id
        if not self.assignment_id:
            raise ValidationError({'assignment': 'A submission must belong to an assignment.'})
        self.clean()
        super().save(*args, **kwargs)

class Grade(models.Model):
    submission = models.OneToOneField(Submission, on_delete=models.CASCADE, related_name='grade')
    graded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='grades_given',
    )
    score = models.DecimalField(max_digits=5, decimal_places=2)
    max_score = models.DecimalField(max_digits=5, decimal_places=2, default=100)
    feedback = models.TextField(blank=True)
    is_released = models.BooleanField(default=False)
    graded_at = models.DateTimeField(auto_now_add=True)
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(score__gte=0) & models.Q(score__lte=models.F('max_score')),
                name='grade_score_within_max',
            ),
        ]

    @property
    def assignment(self):
        return self.submission.assignment

    @property
    def group(self):
        return self.submission.group

    def clean(self):
        if (
            self.score is not None
            and self.max_score is not None
            and (self.score < 0 or self.score > self.max_score)
        ):
            raise ValidationError('Score must be between 0 and the maximum score.')

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class PeerReview(models.Model):
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name='peer_reviews')
    evaluator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='evaluator',
        on_delete=models.CASCADE,
    )
    evaluated = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='evaluated',
        on_delete=models.CASCADE,
    )
    score = models.IntegerField()
    criteria_scores = models.JSONField(default=dict, blank=True)
    comment = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['assignment', 'evaluator', 'evaluated'],
                name='unique_peer_evaluation_per_assignment',
            ),
            models.CheckConstraint(
                condition=~models.Q(evaluator=models.F('evaluated')),
                name='peer_evaluation_no_self_review',
            ),
            models.CheckConstraint(
                condition=models.Q(score__gte=1, score__lte=10),
                name='peer_evaluation_score_1_to_10',
            ),
        ]

    def clean(self):
        if self.evaluator_id and self.evaluated_id and self.evaluator_id == self.evaluated_id:
            raise ValidationError('Students cannot evaluate themselves.')
        if self.score is not None and not 1 <= self.score <= 10:
            raise ValidationError('Score must be between 1 and 10.')
        if self.assignment_id and not self.assignment.peer_evaluation_enabled:
            raise ValidationError('Peer evaluation is not enabled for this assignment.')
        if self.assignment_id and self.evaluator_id and self.evaluated_id:
            same_group = GroupMembership.objects.filter(
                group__assignment_id=self.assignment_id,
                student_id=self.evaluator_id,
                status=GroupMembership.STATUS_APPROVED,
            ).filter(
                group__memberships__student_id=self.evaluated_id,
                group__memberships__status=GroupMembership.STATUS_APPROVED,
            ).exists()
            if not same_group:
                raise ValidationError('Peer evaluations must be between members of the same group.')
            allowed_criteria = {
                key for key, _label in self.assignment.PEER_EVALUATION_CRITERIA
            }
            configured_criteria = set(self.assignment.peer_evaluation_criteria)
            if not configured_criteria.issubset(allowed_criteria):
                raise ValidationError('The assignment contains an unsupported peer evaluation criterion.')
            if self.criteria_scores and set(self.criteria_scores) != configured_criteria:
                raise ValidationError('Peer evaluation scores must match the configured criteria.')
            if any(
                not isinstance(score, int) or not 1 <= score <= 10
                for score in self.criteria_scores.values()
            ):
                raise ValidationError('Each criterion score must be between 1 and 10.')

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class Notification(models.Model):
    TYPE_GENERAL = 'general'
    TYPE_ASSIGNMENT = 'assignment'
    TYPE_MEMBERSHIP = 'membership'
    TYPE_TASK = 'task'
    TYPE_DEADLINE = 'deadline'
    TYPE_MESSAGE = 'message'
    TYPE_DOCUMENT = 'document'
    TYPE_SUBMISSION = 'submission'
    TYPE_FEEDBACK = 'feedback'
    TYPE_CHOICES = [
        (TYPE_GENERAL, 'General'),
        (TYPE_ASSIGNMENT, 'Assignment'),
        (TYPE_MEMBERSHIP, 'Membership'),
        (TYPE_TASK, 'Task'),
        (TYPE_DEADLINE, 'Deadline'),
        (TYPE_MESSAGE, 'Message'),
        (TYPE_DOCUMENT, 'Document'),
        (TYPE_SUBMISSION, 'Submission'),
        (TYPE_FEEDBACK, 'Feedback'),
    ]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    assignment = models.ForeignKey(
        Assignment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notifications',
    )
    group = models.ForeignKey(
        Group,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notifications',
    )
    task = models.ForeignKey(
        Task,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notifications',
    )
    message = models.TextField()
    notification_type = models.CharField(max_length=30, choices=TYPE_CHOICES, default=TYPE_GENERAL)
    created_at = models.DateTimeField(auto_now_add=True)
    read = models.BooleanField(default=False)
    deduplication_key = models.CharField(max_length=255, unique=True, null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'read', 'created_at']),
        ]

    def get_target_url(self):
        from django.urls import reverse

        if self.task_id:
            return reverse('task_detail', args=[self.task_id])
        if self.group_id:
            return reverse('group_detail', args=[self.group_id])
        if self.assignment_id:
            return reverse('assignment_detail', args=[self.assignment_id])
        return ''


class AuditLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='audit_logs',
    )
    action = models.CharField(max_length=200)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']


class ActivityLog(models.Model):
    """Recorded in-app actions. Not a definitive measure of academic effort."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='activity_logs',
    )
    event_type = models.CharField(max_length=50)
    description = models.CharField(max_length=255)
    assignment = models.ForeignKey(
        Assignment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='activity_logs',
    )
    group = models.ForeignKey(
        Group,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='activity_logs',
    )
    task = models.ForeignKey(
        Task,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='activity_logs',
    )
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']


class ContributionActivity(models.Model):
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='contribution_activities',
    )
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='contribution_events_performed',
    )
    assignment = models.ForeignKey(
        Assignment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='contribution_activities',
    )
    group = models.ForeignKey(
        Group,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='contribution_activities',
    )
    task = models.ForeignKey(
        Task,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='contribution_activities',
    )
    document = models.ForeignKey(
        FileDocument,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='contribution_activities',
    )
    submission = models.ForeignKey(
        Submission,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='contribution_activities',
    )
    event_type = models.CharField(max_length=50)
    description = models.CharField(max_length=255)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-occurred_at']
        indexes = [
            models.Index(fields=['student', 'occurred_at']),
            models.Index(fields=['group', 'occurred_at']),
        ]

    def clean(self):
        if self.student_id and self.student.role != User.ROLE_STUDENT:
            raise ValidationError('Contribution activity must belong to a student.')
        if self.task_id and self.group_id and self.task.group_id != self.group_id:
            raise ValidationError('The activity task must belong to the recorded group.')
        if self.document_id and self.group_id and self.document.group_id != self.group_id:
            raise ValidationError('The activity document must belong to the recorded group.')
        if self.submission_id and self.group_id and self.submission.group_id != self.group_id:
            raise ValidationError('The activity submission must belong to the recorded group.')
        if self.group_id and self.assignment_id:
            if self.group.assignment_id != self.assignment_id:
                raise ValidationError('The activity assignment must match its group.')

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Contribution activity records are immutable.')
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.student}: {self.event_type}'
