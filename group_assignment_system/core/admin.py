from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserCreationForm
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from .models import (
    AcademicSession,
    ActivityLog,
    AdministratorProfile,
    Assignment,
    AuditLog,
    ContributionActivity,
    Course,
    CourseEnrolment,
    CourseLecturer,
    FileDocument,
    Grade,
    Group,
    GroupMembership,
    GroupMessage,
    InternalDeadline,
    LecturerProfile,
    Notification,
    PeerReview,
    Semester,
    Submission,
    StudentProfile,
    Task,
    TaskAssignment,
    TaskUpdate,
    User,
)
from .services import log_audit, record_activity


class TaskAdminForm(forms.ModelForm):
    deadline = forms.DateTimeField()

    class Meta:
        model = Task
        fields = (
            'group',
            'title',
            'description',
            'created_by',
            'assigned_to',
            'deadline',
            'priority',
            'status',
            'completed_at',
        )

    def save(self, commit=True):
        task = super().save(commit=False)
        task.deadline = self.cleaned_data['deadline']
        if commit:
            task.save()
            self.save_m2m()
        return task


class CustomUserCreationForm(UserCreationForm):
    email = forms.EmailField(required=False)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'email')


class CourseAdminForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        lecturers = User.objects.filter(role=User.ROLE_LECTURER, is_active=True)
        if self.instance.pk and self.instance.lecturer_id:
            lecturers = (
                lecturers
                | User.objects.filter(
                    pk=self.instance.lecturer_id,
                    role=User.ROLE_LECTURER,
                )
            ).distinct()
        self.fields['lecturer'].queryset = lecturers
        semesters = Semester.objects.filter(
            is_active=True,
            academic_session__is_active=True,
        )
        if self.instance.pk and self.instance.semester_record_id:
            semesters = (semesters | Semester.objects.filter(
                pk=self.instance.semester_record_id
            )).distinct()
        self.fields['semester_record'].queryset = semesters


class ContributionActivityAdmin(admin.ModelAdmin):
    list_display = (
        'student', 'event_type', 'assignment', 'group', 'occurred_at', 'performed_by',
    )
    list_filter = ('event_type', 'occurred_at')
    search_fields = ('student__username', 'student__first_name', 'student__last_name', 'description')
    readonly_fields = (
        'student', 'performed_by', 'assignment', 'group', 'task', 'document',
        'submission', 'event_type', 'description', 'occurred_at',
    )

    def has_add_permission(self, request):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_staff

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class PeerReviewAdmin(admin.ModelAdmin):
    list_display = ('assignment', 'evaluated', 'score', 'created_at')
    list_filter = ('assignment', 'created_at')
    readonly_fields = (
        'assignment', 'evaluator', 'evaluated', 'score', 'criteria_scores',
        'comment', 'created_at',
    )

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.is_superuser:
            return queryset
        managed_courses = Course.objects.filter(
            Q(lecturer=request.user) | Q(lecturer_links__lecturer=request.user)
        )
        return queryset.filter(assignment__course__in=managed_courses).distinct()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    add_form = CustomUserCreationForm
    list_display = ('username', 'email', 'role', 'is_staff', 'is_active')
    list_filter = ('role', 'is_active', 'is_staff')
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        (_('Personal info'), {'fields': ('first_name', 'last_name', 'email')}),
        (_('UNILUS role'), {'fields': ('role',)}),
        (_('Account status'), {'fields': ('is_active',)}),
        (_('Important dates'), {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'password1', 'password2', 'email', 'role'),
        }),
    )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        action = 'Updated' if change else 'Created'
        log_audit(
            request.user,
            f'{action} account {obj.username} with role {obj.get_role_display()}',
        )

    def delete_model(self, request, obj):
        log_audit(request.user, f'Deleted account {obj.username}')
        super().delete_model(request, obj)


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    form = CourseAdminForm
    list_display = ('code', 'name', 'semester', 'academic_year', 'lecturer', 'is_active')
    search_fields = ('code', 'name')


@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display = ('title', 'course', 'deadline', 'status', 'peer_evaluation_enabled')
    list_filter = ('status',)


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'assignment', 'status', 'created_at')


@admin.register(GroupMembership)
class GroupMembershipAdmin(admin.ModelAdmin):
    list_display = ('group', 'student', 'status', 'joined_at')


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    form = TaskAdminForm
    list_display = (
        'title',
        'group',
        'assigned_to',
        'created_by',
        'priority',
        'deadline',
        'status',
        'updated_at',
    )
    list_filter = ('status',)

    def save_model(self, request, obj, form, change):
        if not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
        action = 'Updated' if change else 'Created'
        event_type = 'task_updated' if change else 'task_created'
        record_activity(
            request.user,
            event_type,
            f'{action} task {obj.title or obj.description[:80]}',
            assignment=obj.group.assignment,
            group=obj.group,
            task=obj,
        )
        log_audit(request.user, f'{action} task {obj.pk}')

    def delete_model(self, request, obj):
        record_activity(
            request.user,
            'task_deleted',
            f'Deleted task {obj.title or obj.description[:80]}',
            assignment=obj.group.assignment,
            group=obj.group,
            task=obj,
        )
        log_audit(request.user, f'Deleted task {obj.pk}')
        super().delete_model(request, obj)


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ('assignment', 'group', 'submitted_by', 'submitted_at', 'status', 'is_latest')


@admin.register(Grade)
class GradeAdmin(admin.ModelAdmin):
    list_display = ('group', 'score', 'max_score', 'is_released', 'graded_by', 'graded_at')


admin.site.register(AcademicSession)
admin.site.register(Semester)
admin.site.register(CourseLecturer)
admin.site.register(CourseEnrolment)
admin.site.register(TaskUpdate)
admin.site.register(StudentProfile)
admin.site.register(LecturerProfile)
admin.site.register(AdministratorProfile)
admin.site.register(TaskAssignment)
admin.site.register(InternalDeadline)
admin.site.register(FileDocument)
admin.site.register(GroupMessage)
admin.site.register(PeerReview, PeerReviewAdmin)
admin.site.register(Notification)
admin.site.register(AuditLog)
admin.site.register(ActivityLog)
admin.site.register(ContributionActivity, ContributionActivityAdmin)
