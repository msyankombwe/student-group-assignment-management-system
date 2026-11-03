import csv
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, Max, Prefetch, Q
from django.middleware.csrf import get_token
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import get_valid_filename
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status
from rest_framework import viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError as APIValidationError
from rest_framework.views import APIView
from rest_framework.response import Response

from .forms import (
    AssignmentForm,
    CourseForm,
    EvidenceUploadForm,
    GradeForm,
    GroupDocumentUploadForm,
    GroupForm,
    GroupInvitationForm,
    GroupMessageForm,
    GroupProposalForm,
    GroupUpdateForm,
    MembershipForm,
    PeerReviewForm,
    SubmissionForm,
    TaskForm,
    TaskStatusForm,
    TaskUpdateForm,
)
from .models import (
    ActivityLog,
    Assignment,
    AuditLog,
    Course,
    ContributionActivity,
    FileDocument,
    Grade,
    Group,
    GroupMembership,
    GroupMessage,
    Notification,
    PeerReview,
    Submission,
    Task,
    TaskUpdate,
    User,
)
from .permissions import (
    ScopedRolePermission,
    SystemAdministratorPermission,
    TaskManagementPermission,
)
from .serializers import (
    AdminUserSerializer,
    AssignmentSerializer,
    CourseSerializer,
    GroupSerializer,
    TaskSerializer,
    TaskUpdateSerializer,
    UserSerializer,
)
from .services import (
    can_manage_academic,
    log_audit,
    notify,
    record_activity,
    user_can_access_group,
    user_can_access_group_content,
    user_is_group_member,
    user_manages_course,
    user_manages_assignment,
)


class RoleLoginView(LoginView):
    template_name = 'core/login.html'


@api_view(['GET'])
@permission_classes([ScopedRolePermission])
def api_current_user(request):
    return Response(UserSerializer(request.user).data)


@ensure_csrf_cookie
@api_view(['GET'])
def api_csrf_token(request):
    return Response({'csrfToken': get_token(request)})
    redirect_authenticated_user = True


class RoleLogoutView(LogoutView):
    next_page = 'login'


class AdminDashboardSummaryAPIView(APIView):
    permission_classes = [SystemAdministratorPermission]

    def get(self, request):
        recent_cutoff = timezone.now() - timedelta(days=30)
        return Response({
            'users': {
                'total': User.objects.count(),
                'active': User.objects.filter(is_active=True).count(),
                'students': User.objects.filter(role=User.ROLE_STUDENT).count(),
                'lecturers': User.objects.filter(role=User.ROLE_LECTURER).count(),
                'administrators': User.objects.filter(
                    Q(role=User.ROLE_ADMIN) | Q(is_superuser=True)
                ).distinct().count(),
            },
            'courses': {
                'total': Course.objects.count(),
                'active': Course.objects.filter(is_active=True).count(),
            },
            'assignments': {
                'total': Assignment.objects.count(),
                'published': Assignment.objects.filter(
                    status=Assignment.STATUS_PUBLISHED,
                ).count(),
                'draft': Assignment.objects.filter(
                    status=Assignment.STATUS_DRAFT,
                ).count(),
            },
            'groups': {
                'total': Group.objects.count(),
                'active': Group.objects.filter(status=Group.STATUS_ACTIVE).count(),
                'pending': Group.objects.filter(status=Group.STATUS_PENDING).count(),
            },
            'submissions': {
                'latest': Submission.objects.filter(is_latest=True).count(),
                'awaiting_review': Submission.objects.filter(
                    is_latest=True,
                    grade__isnull=True,
                ).count(),
            },
            'activity': {
                'recent_actions': (
                    ActivityLog.objects.filter(
                        timestamp__gte=recent_cutoff,
                    ).count()
                    + AuditLog.objects.filter(
                        timestamp__gte=recent_cutoff,
                    ).count()
                ),
            },
        })


class AdminActivityAPIView(APIView):
    permission_classes = [SystemAdministratorPermission]

    def get(self, request):
        limit_value = request.query_params.get('limit', '50')
        if not limit_value.isdecimal():
            return Response(
                {'detail': 'Limit must be a positive integer.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        limit = int(limit_value)
        if limit < 1 or limit > 100:
            return Response(
                {'detail': 'Limit must be between 1 and 100.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        activities = [
            {
                'id': f'activity-{entry.pk}',
                'category': entry.event_type,
                'user': entry.user.get_full_name() or entry.user.username,
                'action': entry.description,
                'timestamp': entry.timestamp,
                'assignment': entry.assignment.title if entry.assignment_id else '',
                'group': entry.group.name if entry.group_id else '',
            }
            for entry in ActivityLog.objects.select_related(
                'user', 'assignment', 'group',
            ).order_by('-timestamp')[:limit]
        ]
        activities.extend(
            {
                'id': f'audit-{entry.pk}',
                'category': 'administrative_audit',
                'user': entry.user.get_full_name() or entry.user.username,
                'action': entry.action,
                'timestamp': entry.timestamp,
                'assignment': '',
                'group': '',
            }
            for entry in AuditLog.objects.select_related('user').order_by(
                '-timestamp',
            )[:limit]
        )
        activities.sort(key=lambda entry: entry['timestamp'], reverse=True)
        return Response([
            {
                **entry,
                'timestamp': entry['timestamp'].isoformat(),
            }
            for entry in activities[:limit]
        ])


def _set_group_membership(group, student, status, invited_by=None):
    membership = GroupMembership.objects.filter(
        assignment=group.assignment,
        student=student,
    ).first()
    if membership and membership.status != GroupMembership.STATUS_REJECTED:
        raise ValidationError('This student already has a group membership or request.')
    if membership is None:
        membership = GroupMembership(
            assignment=group.assignment,
            group=group,
            student=student,
        )
    membership.group = group
    membership.status = status
    membership.invited_by = invited_by
    membership.save()
    return membership


def _spreadsheet_safe_cell(value):
    if isinstance(value, str):
        formula_candidate = value.lstrip()
        if value.startswith(('\t', '\r')) or formula_candidate.startswith(
            ('=', '+', '-', '@')
        ):
            return f"'{value}"
    return value


def _group_contribution_summaries(groups, activity_cutoff):
    groups = list(groups)
    if not groups:
        return []

    group_ids = [group.pk for group in groups]
    memberships = GroupMembership.objects.filter(
        group_id__in=group_ids,
        status=GroupMembership.STATUS_APPROVED,
    ).select_related('student')
    members_by_group = {}
    for membership in memberships:
        members_by_group.setdefault(membership.group_id, []).append(membership.student)

    task_counts = {
        (row['group_id'], row['assigned_to_id']): row
        for row in Task.objects.filter(group_id__in=group_ids)
        .values('group_id', 'assigned_to_id')
        .annotate(
            assigned=Count('pk'),
            completed=Count('pk', filter=Q(status=Task.STATUS_COMPLETED)),
        )
    }
    group_task_counts = {
        row['group_id']: row
        for row in Task.objects.filter(group_id__in=group_ids)
        .values('group_id')
        .annotate(
            total=Count('pk'),
            completed=Count('pk', filter=Q(status=Task.STATUS_COMPLETED)),
        )
    }
    file_counts = {
        (row['group_id'], row['uploaded_by_id']): row['count']
        for row in FileDocument.objects.filter(
            group_id__in=group_ids,
            document_type=FileDocument.TYPE_GROUP_FILE,
        ).values('group_id', 'uploaded_by_id').annotate(count=Count('pk'))
    }
    discussion_counts = {
        (row['group_id'], row['author_id']): row['count']
        for row in GroupMessage.objects.filter(group_id__in=group_ids)
        .values('group_id', 'author_id')
        .annotate(count=Count('pk'))
    }
    activity_counts = {
        (row['group_id'], row['student_id']): row
        for row in ContributionActivity.objects.filter(
            group_id__in=group_ids,
        ).values('group_id', 'student_id').annotate(
            total=Count('pk'),
            recent=Count('pk', filter=Q(occurred_at__gte=activity_cutoff)),
            last_activity=Max('occurred_at'),
        )
    }

    summaries = []
    for group in groups:
        member_summaries = []
        for student in members_by_group.get(group.pk, []):
            task_data = task_counts.get((group.pk, student.pk), {})
            activity_data = activity_counts.get((group.pk, student.pk), {})
            member_summaries.append({
                'student': student,
                'tasks_assigned': task_data.get('assigned', 0),
                'tasks_completed': task_data.get('completed', 0),
                'tasks_pending': task_data.get('assigned', 0) - task_data.get('completed', 0),
                'files_uploaded': file_counts.get((group.pk, student.pk), 0),
                'discussion_messages': discussion_counts.get((group.pk, student.pk), 0),
                'activity_count': activity_data.get('total', 0),
                'recent_activity_count': activity_data.get('recent', 0),
                'last_activity': activity_data.get('last_activity'),
                'no_recent_activity': activity_data.get('recent', 0) == 0,
            })
        group_tasks = group_task_counts.get(group.pk, {})
        task_total = group_tasks.get('total', 0)
        task_completed = group_tasks.get('completed', 0)
        summaries.append({
            'group': group,
            'members': member_summaries,
            'task_total': task_total,
            'task_completed': task_completed,
            'completion_percent': round(task_completed * 100 / task_total) if task_total else 0,
        })
    return summaries


def _student_contribution_summary(user):
    group_ids = GroupMembership.objects.filter(
        student=user,
        status=GroupMembership.STATUS_APPROVED,
        group__status__in=(Group.STATUS_ACTIVE, Group.STATUS_CLOSED),
    ).values_list('group_id', flat=True)
    tasks = Task.objects.filter(group_id__in=group_ids, assigned_to=user)
    task_count = tasks.count()
    completed = tasks.filter(status=Task.STATUS_COMPLETED).count()
    return {
        'tasks_assigned': task_count,
        'tasks_completed': completed,
        'tasks_pending': task_count - completed,
        'files_uploaded': FileDocument.objects.filter(
            group_id__in=group_ids,
            uploaded_by=user,
            document_type=FileDocument.TYPE_GROUP_FILE,
        ).count(),
        'discussion_messages': GroupMessage.objects.filter(
            group_id__in=group_ids,
            author=user,
        ).count(),
    }


def _notify_assignment_published(assignment):
    if not assignment.is_published():
        return
    for enrolment in assignment.course.enrolments.select_related('student'):
        notify(
            enrolment.student,
            f'New assignment published: {assignment.title}',
            Notification.TYPE_ASSIGNMENT,
            assignment=assignment,
            deduplication_key=(
                f'assignment-published:{assignment.pk}:user:{enrolment.student_id}'
            ),
        )


@login_required
def dashboard(request):
    user = request.user
    can_manage = can_manage_academic(user)

    if user.is_admin_role():
        dashboard_role = 'administrator'
        group_requests = ()
        courses = Course.objects.filter(is_active=True).prefetch_related('assignments__groups')
        groups = Group.objects.select_related('assignment', 'leader').prefetch_related(
            'memberships__student'
        ).all()
        task_queryset = Task.objects.all()
        contribution_activities = ContributionActivity.objects.select_related(
            'student', 'performed_by', 'assignment', 'group', 'task', 'document'
        )[:20]
        contribution_summaries = ()
    elif user.is_lecturer():
        dashboard_role = 'lecturer'
        group_requests = ()
        courses = Course.objects.filter(
            Q(lecturer=user) | Q(lecturer_links__lecturer=user),
            is_active=True,
        ).distinct().prefetch_related(
            Prefetch(
                'assignments',
                queryset=Assignment.objects.filter(
                    Q(
                        status=Assignment.STATUS_PUBLISHED,
                        published_at__lte=timezone.now(),
                    )
                    | Q(status__in=(Assignment.STATUS_DRAFT, Assignment.STATUS_CLOSED))
                ),
            )
        )
        assignments = Assignment.objects.filter(
            Q(course__lecturer=user) | Q(course__lecturer_links__lecturer=user)
        )
        groups = Group.objects.filter(
            assignment__in=assignments
        ).select_related('assignment', 'leader').prefetch_related('memberships__student')
        task_queryset = Task.objects.filter(group__assignment__in=assignments)
        contribution_activities = ContributionActivity.objects.filter(
            assignment__in=assignments
        ).select_related(
            'student', 'performed_by', 'assignment', 'group', 'task', 'document'
        )[:20]
        contribution_summaries = _group_contribution_summaries(
            groups.select_related('assignment__course', 'leader'),
            timezone.now() - timedelta(days=30),
        )
    else:
        dashboard_role = 'student'
        group_requests = GroupMembership.objects.filter(
            student=user,
            status__in=(GroupMembership.STATUS_PENDING, GroupMembership.STATUS_INVITED),
        ).select_related('group__assignment')
        memberships = GroupMembership.objects.filter(
            student=user,
            status=GroupMembership.STATUS_APPROVED,
            group__status__in=(Group.STATUS_ACTIVE, Group.STATUS_CLOSED),
        ).select_related('group__assignment__course', 'group__leader').prefetch_related(
            'group__memberships__student'
        )
        groups = [m.group for m in memberships]
        course_ids = {m.group.assignment.course_id for m in memberships}
        enrolled_ids = set(user.course_enrolments.values_list('course_id', flat=True))
        courses = Course.objects.filter(
            id__in=course_ids | enrolled_ids,
            is_active=True,
        ).prefetch_related(
            Prefetch(
                'assignments',
                queryset=Assignment.objects.filter(
                    status=Assignment.STATUS_PUBLISHED,
                    published_at__lte=timezone.now(),
                ),
            )
        )
        task_queryset = Task.objects.filter(
            group__memberships__student=user,
            group__memberships__status=GroupMembership.STATUS_APPROVED,
            group__status__in=(Group.STATUS_ACTIVE, Group.STATUS_CLOSED),
        ).distinct()
        contribution_activities = ContributionActivity.objects.filter(
            student=user
        ).select_related(
            'performed_by', 'assignment', 'group', 'task', 'document'
        )[:20]
        contribution_summaries = ()

    now = timezone.now()
    task_queryset = task_queryset.select_related(
        'group__assignment',
        'assigned_to',
        'internal_deadline',
    )
    metric_task_queryset = task_queryset
    overdue_filter = metric_task_queryset.filter(
        internal_deadline__due_at__lt=now,
    ).exclude(status=Task.STATUS_COMPLETED)
    upcoming_tasks = metric_task_queryset.filter(
        internal_deadline__due_at__gte=now,
    ).exclude(status=Task.STATUS_COMPLETED).order_by('internal_deadline__due_at')[:8]
    task_metrics = {
        'total': metric_task_queryset.count(),
        'completed': metric_task_queryset.filter(status=Task.STATUS_COMPLETED).count(),
        'pending': metric_task_queryset.exclude(status=Task.STATUS_COMPLETED).count(),
        'overdue': overdue_filter.count(),
        'assigned_to_me': task_queryset.filter(assigned_to=user).count()
        if user.is_student()
        else 0,
    }
    overdue_tasks = overdue_filter.order_by('internal_deadline__due_at')[:8]
    tasks = task_queryset.order_by('internal_deadline__due_at')[:30]
    delayed_group_ids = overdue_filter.values_list('group_id', flat=True).distinct()
    if isinstance(groups, list):
        delayed_group_ids = set(delayed_group_ids)
        delayed_groups = [
            group
            for group in groups
            if group.pk in delayed_group_ids
        ]
    else:
        delayed_groups = groups.filter(pk__in=delayed_group_ids)
    if user.is_admin_role():
        groups = groups[:50]
    notifications = Notification.objects.filter(user=user, read=False)[:8]

    if user.is_admin_role():
        submissions = Submission.objects.filter(is_latest=True).select_related(
            'group__assignment'
        )[:10]
    elif user.is_lecturer():
        submissions = Submission.objects.filter(
            is_latest=True,
        ).filter(
            Q(group__assignment__course__lecturer=user)
            | Q(group__assignment__course__lecturer_links__lecturer=user)
        ).select_related('group__assignment').distinct()[:10]
    else:
        submissions = Submission.objects.filter(
            group__memberships__student=user,
            group__memberships__status=GroupMembership.STATUS_APPROVED,
            group__status__in=(Group.STATUS_ACTIVE, Group.STATUS_CLOSED),
            is_latest=True,
        ).select_related('group__assignment').distinct()[:10]

    context = {
        'courses': courses,
        'groups': groups,
        'group_requests': group_requests,
        'tasks': tasks,
        'task_metrics': task_metrics,
        'notifications': notifications,
        'can_manage': can_manage,
        'overdue_tasks': overdue_tasks,
        'upcoming_tasks': upcoming_tasks,
        'delayed_groups': delayed_groups,
        'submissions': submissions,
        'stats': _dashboard_stats(user),
        'dashboard_role': dashboard_role,
        'contribution_activities': contribution_activities,
        'contribution_summaries': contribution_summaries,
        'student_contribution_summary': (
            _student_contribution_summary(user) if user.is_student() else None
        ),
        'contribution_cutoff': now - timedelta(days=30),
        'report_types': (
            ('submission', 'Assignment submission report'),
            ('progress', 'Group progress report'),
            ('contribution', 'Student contribution report'),
            ('inactive', 'Inactive group report'),
            ('student_tasks', 'Student task report'),
            ('performance', 'Group performance summary'),
            ('admin_activity', 'Administrative activity report'),
            ('course_summary', 'Course assignment summary'),
        ),
    }
    return render(request, 'core/dashboard.html', context)


@login_required
def reports(request):
    user = request.user
    if user.is_admin_role() or user.is_superuser:
        courses = Course.objects.all()
        groups = Group.objects.all()
        report_types = (
            'submission', 'progress', 'contribution', 'inactive',
            'student_tasks', 'performance', 'admin_activity', 'course_summary',
        )
    elif user.is_lecturer():
        courses = _managed_courses_for(user)
        groups = Group.objects.filter(assignment__course__in=courses).distinct()
        report_types = (
            'submission', 'progress', 'contribution', 'inactive',
            'student_tasks', 'performance', 'course_summary',
        )
    elif user.is_student():
        courses = Course.objects.filter(
            Q(enrolments__student=user)
            | Q(
                assignments__groups__memberships__student=user,
                assignments__groups__memberships__status=GroupMembership.STATUS_APPROVED,
            ),
        ).distinct()
        groups = Group.objects.filter(
            memberships__student=user,
            memberships__status=GroupMembership.STATUS_APPROVED,
            status__in=(Group.STATUS_ACTIVE, Group.STATUS_CLOSED),
        ).distinct()
        report_types = ('submission', 'contribution', 'student_tasks')
    else:
        return _forbidden(request)

    report_type = request.GET.get('type', 'submission')
    if report_type not in report_types:
        return _forbidden(request)

    assignments = Assignment.objects.filter(course__in=courses)
    if user.is_student():
        assignments = assignments.filter(
            Q(course__enrolments__student=user)
            | Q(
                groups__memberships__student=user,
                groups__memberships__status=GroupMembership.STATUS_APPROVED,
            ),
        ).distinct()
    scoped_groups = groups.select_related('assignment__course')
    selected_course = request.GET.get('course', '')
    selected_assignment = request.GET.get('assignment', '')
    selected_group = request.GET.get('group', '')
    selected_semester = request.GET.get('semester', '')
    selected_year = request.GET.get('year_of_study', '')

    def valid_filter(value, queryset):
        return value if value.isdecimal() and queryset.filter(pk=value).exists() else ''

    requested_filters = (
        (selected_course, courses),
        (selected_assignment, assignments),
        (selected_group, scoped_groups),
    )
    if any(value and not valid_filter(value, queryset) for value, queryset in requested_filters):
        return _forbidden(request)
    if selected_semester and (
        not selected_semester.isdecimal()
        or int(selected_semester) not in dict(Course.SEMESTER_CHOICES)
    ):
        return _forbidden(request)
    if selected_year and (
        not selected_year.isdecimal()
        or int(selected_year) not in dict(Course.YEAR_OF_STUDY_CHOICES)
    ):
        return _forbidden(request)
    selected_course = valid_filter(selected_course, courses)
    selected_assignment = valid_filter(selected_assignment, assignments)
    selected_group = valid_filter(selected_group, scoped_groups)

    if selected_course:
        assignments = assignments.filter(course_id=selected_course)
        scoped_groups = scoped_groups.filter(assignment__course_id=selected_course)
        courses = courses.filter(pk=selected_course)
    if selected_assignment:
        scoped_groups = scoped_groups.filter(assignment_id=selected_assignment)
        assignments = assignments.filter(pk=selected_assignment)
        courses = courses.filter(assignments__pk=selected_assignment).distinct()
    if selected_group:
        scoped_groups = scoped_groups.filter(pk=selected_group)
        assignments = assignments.filter(groups__pk=selected_group).distinct()
        courses = courses.filter(assignments__groups__pk=selected_group).distinct()
    if selected_semester:
        scoped_groups = scoped_groups.filter(
            assignment__course__semester=int(selected_semester),
        )
        assignments = assignments.filter(course__semester=int(selected_semester))
        courses = courses.filter(semester=int(selected_semester))
    if selected_year:
        scoped_groups = scoped_groups.filter(
            assignment__course__year_of_study=int(selected_year),
        )
        assignments = assignments.filter(course__year_of_study=int(selected_year))
        courses = courses.filter(year_of_study=int(selected_year))
    scoped_groups = scoped_groups.distinct()
    assignments = assignments.distinct()
    title_map = {
        'submission': 'Assignment Submission Report',
        'progress': 'Group Progress Report',
        'contribution': 'Student Contribution Report',
        'inactive': 'Inactive Group Report',
        'student_tasks': 'Student Task Report',
        'performance': 'Group Performance Summary',
        'admin_activity': 'Administrative Activity Report',
        'course_summary': 'Course Assignment Summary',
    }
    headers = []
    rows = []

    if report_type == 'submission':
        headers = ['Course', 'Assignment', 'Group', 'Student', 'Submitted at', 'Status', 'Late']
        for group in scoped_groups.filter(status__in=(Group.STATUS_ACTIVE, Group.STATUS_CLOSED)):
            submission = group.submissions.filter(is_latest=True).select_related(
                'submitted_by',
            ).first()
            rows.append([
                group.assignment.course.code or group.assignment.course.name,
                group.assignment.title,
                group.name,
                submission.submitted_by.get_full_name() or submission.submitted_by.username
                if submission else '—',
                submission.submitted_at.strftime('%Y-%m-%d %H:%M:%S') if submission else '—',
                submission.get_status_display() if submission else 'Not Submitted',
                'Yes' if submission and submission.is_late else 'No',
            ])
    elif report_type in ('progress', 'performance', 'inactive'):
        headers = ['Course', 'Assignment', 'Group', 'Members', 'Tasks', 'Completed', 'Overdue', 'Progress %', 'Submission']
        cutoff = timezone.now() - timedelta(days=30)
        for group in scoped_groups.filter(status__in=(Group.STATUS_ACTIVE, Group.STATUS_CLOSED)):
            task_qs = group.tasks.all()
            total = task_qs.count()
            completed = task_qs.filter(status=Task.STATUS_COMPLETED).count()
            overdue = task_qs.filter(
                internal_deadline__due_at__lt=timezone.now(),
            ).exclude(status=Task.STATUS_COMPLETED).count()
            last_activity = group.contribution_activities.order_by('-occurred_at').values_list(
                'occurred_at', flat=True
            ).first()
            if report_type == 'inactive' and last_activity and last_activity >= cutoff:
                continue
            submission_status = 'Submitted' if group.submissions.filter(is_latest=True).exists() else 'Not Submitted'
            rows.append([
                group.assignment.course.code or group.assignment.course.name,
                group.assignment.title,
                group.name,
                group.memberships.filter(status=GroupMembership.STATUS_APPROVED).count(),
                total,
                completed,
                overdue,
                round(completed * 100 / total) if total else 0,
                submission_status,
            ])
    elif report_type == 'student_tasks':
        headers = ['Course', 'Assignment', 'Group', 'Task', 'Student', 'Status', 'Priority', 'Deadline']
        tasks = Task.objects.filter(group__in=scoped_groups).select_related(
            'group__assignment__course', 'assigned_to', 'internal_deadline',
        ).order_by('internal_deadline__due_at')
        if user.is_student():
            tasks = tasks.filter(assigned_to=user)
        for task in tasks:
            rows.append([
                task.group.assignment.course.code or task.group.assignment.course.name,
                task.group.assignment.title,
                task.group.name,
                task.title or task.description[:80],
                task.assigned_to.get_full_name() or task.assigned_to.username,
                task.effective_status_display,
                task.get_priority_display(),
                task.deadline.strftime('%Y-%m-%d %H:%M') if task.deadline else '—',
            ])
    elif report_type == 'contribution':
        summaries = _group_contribution_summaries(
            scoped_groups.filter(status__in=(Group.STATUS_ACTIVE, Group.STATUS_CLOSED)),
            timezone.now() - timedelta(days=30),
        )
        headers = ['Course', 'Assignment', 'Group', 'Student', 'Tasks assigned', 'Completed', 'Pending', 'Files', 'Messages', 'Recorded activities']
        for summary in summaries:
            for member in summary['members']:
                if user.is_student() and member['student'].pk != user.pk:
                    continue
                rows.append([
                    summary['group'].assignment.course.code or summary['group'].assignment.course.name,
                    summary['group'].assignment.title,
                    summary['group'].name,
                    member['student'].get_full_name() or member['student'].username,
                    member['tasks_assigned'],
                    member['tasks_completed'],
                    member['tasks_pending'],
                    member['files_uploaded'],
                    member['discussion_messages'],
                    member['activity_count'],
                ])
    elif report_type == 'course_summary':
        headers = ['Course', 'Year of study', 'Semester', 'Assignments', 'Groups', 'Submissions', 'Late submissions']
        for course in courses:
            course_assignments = assignments.filter(course=course)
            course_groups = scoped_groups.filter(assignment__course=course)
            rows.append([
                course.code or course.name,
                course.get_year_of_study_display() or 'Not classified',
                course.get_semester_display() or 'Not classified',
                course_assignments.count(),
                course_groups.count(),
                Submission.objects.filter(is_latest=True, group__in=course_groups).count(),
                Submission.objects.filter(
                    is_latest=True, group__in=course_groups,
                    submitted_at__gt=F('group__assignment__deadline'),
                ).count(),
            ])
    elif report_type == 'admin_activity':
        if not (user.is_admin_role() or user.is_superuser):
            return _forbidden(request)
        headers = ['Date/time', 'User', 'Action', 'Event type', 'Assignment', 'Group']
        activity_scope = Q(assignment__in=assignments) | Q(group__in=scoped_groups)
        if not any((selected_course, selected_assignment, selected_group, selected_semester, selected_year)):
            activity_scope |= Q(assignment__isnull=True, group__isnull=True)
        activities = ActivityLog.objects.filter(
            activity_scope,
        ).select_related('user', 'assignment', 'group').order_by('-timestamp')[:500]
        for activity in activities:
            rows.append([
                activity.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                activity.user.username,
                activity.description,
                activity.event_type,
                activity.assignment.title if activity.assignment_id else '—',
                activity.group.name if activity.group_id else '—',
            ])
        if not any((selected_course, selected_assignment, selected_group, selected_semester, selected_year)):
            for entry in AuditLog.objects.select_related('user').order_by('-timestamp')[:500]:
                rows.append([
                    entry.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                    entry.user.username,
                    entry.action,
                    'Administrative audit',
                    '—',
                    '—',
                ])
            rows.sort(key=lambda row: row[0], reverse=True)
            rows = rows[:500]

    if request.GET.get('format') == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = (
            f'attachment; filename="{report_type}-report.csv"'
        )
        writer = csv.writer(response)
        writer.writerow(headers)
        for row in rows:
            writer.writerow([_spreadsheet_safe_cell(cell) for cell in row])
        return response

    filter_courses = courses.order_by('code', 'name')
    filter_assignments = assignments.select_related('course').order_by('title')
    filter_groups = scoped_groups.order_by('name')
    semesters = Course.SEMESTER_CHOICES
    years_of_study = Course.YEAR_OF_STUDY_CHOICES
    return render(request, 'core/reports.html', {
        'report_types': [(key, title_map[key]) for key in report_types],
        'report_type': report_type,
        'title': title_map[report_type],
        'headers': headers,
        'rows': rows,
        'courses': filter_courses,
        'assignments': filter_assignments,
        'groups': filter_groups,
        'semesters': semesters,
        'years_of_study': years_of_study,
        'selected_course': selected_course,
        'selected_assignment': selected_assignment,
        'selected_group': selected_group,
        'selected_semester': selected_semester,
        'selected_year': selected_year,
        'chart_labels': [row[2] for row in rows] if report_type in ('progress', 'performance') else [],
        'chart_values': [row[7] for row in rows] if report_type in ('progress', 'performance') else [],
    })


@login_required
def contribution_report(request):
    user = request.user
    if user.is_admin_role() or user.is_superuser:
        groups = Group.objects.select_related(
            'assignment__course', 'leader'
        ).prefetch_related('memberships__student')
    elif user.is_lecturer():
        managed_courses = Course.objects.filter(
            Q(lecturer=user) | Q(lecturer_links__lecturer=user)
        )
        groups = Group.objects.filter(
            assignment__course__in=managed_courses,
        ).select_related(
            'assignment__course', 'leader'
        ).prefetch_related('memberships__student').distinct()
    else:
        return _forbidden(request)

    cutoff = timezone.now() - timedelta(days=30)
    summaries = _group_contribution_summaries(groups, cutoff)
    if request.GET.get('format') == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="contribution-report.csv"'
        writer = csv.writer(response)
        writer.writerow([
            'Course', 'Assignment', 'Group', 'Student', 'Tasks assigned',
            'Tasks completed', 'Tasks pending', 'Files uploaded',
            'Discussion messages', 'Recorded activities', 'Recent activities (30 days)',
            'Last recorded activity',
        ])
        for summary in summaries:
            for member in summary['members']:
                writer.writerow([_spreadsheet_safe_cell(cell) for cell in [
                    summary['group'].assignment.course.code,
                    summary['group'].assignment.title,
                    summary['group'].name,
                    member['student'].get_full_name() or member['student'].username,
                    member['tasks_assigned'],
                    member['tasks_completed'],
                    member['tasks_pending'],
                    member['files_uploaded'],
                    member['discussion_messages'],
                    member['activity_count'],
                    member['recent_activity_count'],
                    member['last_activity'].isoformat() if member['last_activity'] else '',
                ]])
        return response

    return render(request, 'core/contribution_report.html', {
        'contribution_summaries': summaries,
        'contribution_cutoff': cutoff,
    })


def _dashboard_stats(user):
    if user.is_admin_role() or user.is_superuser:
        return {
            'users_by_role': {
                'students': User.objects.filter(role=User.ROLE_STUDENT).count(),
                'lecturers': User.objects.filter(role=User.ROLE_LECTURER).count(),
                'admins': User.objects.filter(role=User.ROLE_ADMIN).count(),
            },
            'courses': Course.objects.count(),
            'assignments': Assignment.objects.count(),
            'groups': Group.objects.count(),
            'submissions': Submission.objects.filter(is_latest=True).count(),
            'active_users': User.objects.filter(is_active=True).count(),
            'recent_activities': ActivityLog.objects.filter(
                timestamp__gte=timezone.now() - timedelta(days=30)
            ).count(),
        }
    if user.is_lecturer():
        managed_courses = Course.objects.filter(
            Q(lecturer=user) | Q(lecturer_links__lecturer=user)
        ).distinct()
        managed_groups = Group.objects.filter(assignment__course__in=managed_courses)
        active_groups = managed_groups.filter(
            status__in=(Group.STATUS_ACTIVE, Group.STATUS_CLOSED),
        )
        return {
            'courses': managed_courses.count(),
            'assignments': Assignment.objects.filter(course__in=managed_courses).count(),
            'active_assignments': Assignment.objects.filter(
                course__in=managed_courses,
                status=Assignment.STATUS_PUBLISHED,
                published_at__lte=timezone.now(),
                deadline__gte=timezone.now(),
            ).count(),
            'groups': managed_groups.count(),
            'submissions': Submission.objects.filter(
                group__assignment__course__in=managed_courses, is_latest=True
            ).count(),
            'incomplete_groups': active_groups.annotate(
                total_tasks=Count('tasks'),
                completed_tasks=Count(
                    'tasks',
                    filter=Q(tasks__status=Task.STATUS_COMPLETED),
                ),
            ).filter(total_tasks__gt=F('completed_tasks')).count(),
            'overdue_groups': active_groups.filter(
                pk__in=Task.objects.filter(
                    group__in=active_groups,
                    internal_deadline__due_at__lt=timezone.now(),
                ).exclude(status=Task.STATUS_COMPLETED).values('group_id').distinct()
            ).count(),
            'outstanding_submissions': active_groups.filter(
                assignment__deadline__gte=timezone.now(),
            ).exclude(submissions__is_latest=True).distinct().count(),
        }
    student_courses = Course.objects.filter(
        Q(enrolments__student=user)
        | Q(assignments__groups__memberships__student=user,
            assignments__groups__memberships__status=GroupMembership.STATUS_APPROVED),
    ).distinct()
    return {
        'courses': student_courses.count(),
        'active_assignments': Assignment.objects.filter(
            course__in=student_courses,
            status=Assignment.STATUS_PUBLISHED,
            published_at__lte=timezone.now(),
            deadline__gte=timezone.now(),
        ).distinct().count(),
        'groups': GroupMembership.objects.filter(
            student=user,
            status=GroupMembership.STATUS_APPROVED,
            group__status__in=(Group.STATUS_ACTIVE, Group.STATUS_CLOSED),
        ).count(),
        'tasks': Task.objects.filter(assigned_to=user).exclude(status=Task.STATUS_COMPLETED).count(),
        'overdue': Task.objects.filter(assigned_to=user)
        .exclude(status=Task.STATUS_COMPLETED)
        .filter(internal_deadline__due_at__lt=timezone.now())
        .count(),
    }


@login_required
def course_create(request):
    if not request.user.is_admin_role():
        return _forbidden(request)
    form = CourseForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        course = form.save()
        log_audit(request.user, f'Created course {course.name}')
        record_activity(request.user, 'course_created', f'Created course {course.name}')
        messages.success(request, 'Course created.')
        return redirect('course_detail', course.pk)
    return render(request, 'core/course_form.html', {
        'title': 'Create course',
        'form': form,
    })


@login_required
def group_list(request):
    user = request.user
    groups = Group.objects.select_related(
        'assignment__course',
        'leader',
    ).prefetch_related('memberships__student')
    if user.is_admin_role() or user.is_superuser:
        pass
    elif user.is_lecturer():
        groups = groups.filter(
            assignment__course__in=_managed_courses_for(user),
        )
    elif user.is_student():
        groups = groups.filter(
            memberships__student=user,
            memberships__status__in=(
                GroupMembership.STATUS_APPROVED,
                GroupMembership.STATUS_PENDING,
                GroupMembership.STATUS_INVITED,
            ),
        )
    else:
        return _forbidden(request)

    section = request.GET.get('section', '')
    if section not in ('', 'documents', 'discussions'):
        section = ''
    paginator = Paginator(groups.distinct().order_by(
        'assignment__deadline',
        'name',
    ), 24)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'core/group_list.html', {
        'groups': page_obj.object_list,
        'page_obj': page_obj,
        'section': section,
    })


@login_required
def task_list(request):
    user = request.user
    scope = request.GET.get('scope', 'mine' if user.is_student() else 'group')
    tasks = Task.objects.select_related(
        'group__assignment__course',
        'assigned_to',
        'internal_deadline',
    )
    if user.is_admin_role() or user.is_superuser:
        pass
    elif user.is_lecturer():
        tasks = tasks.filter(
            group__assignment__course__in=_managed_courses_for(user),
        )
    elif user.is_student():
        tasks = tasks.filter(
            group__memberships__student=user,
            group__memberships__status=GroupMembership.STATUS_APPROVED,
            group__status__in=(Group.STATUS_ACTIVE, Group.STATUS_CLOSED),
        )
        tasks = tasks.distinct()
        my_task_count = tasks.filter(assigned_to=user).count()
        group_task_count = tasks.count()
        if scope != 'mine':
            scope = 'group'
        if scope == 'mine':
            tasks = tasks.filter(assigned_to=user)
    else:
        return _forbidden(request)

    tasks = tasks.distinct()
    paginator = Paginator(tasks.order_by(
        'internal_deadline__due_at',
        'pk',
    ), 25)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'core/task_list.html', {
        'tasks': page_obj.object_list,
        'page_obj': page_obj,
        'scope': scope,
        'my_task_count': my_task_count if user.is_student() else None,
        'group_task_count': group_task_count if user.is_student() else None,
    })


def _managed_courses_for(user):
    return Course.objects.filter(
        Q(lecturer=user) | Q(lecturer_links__lecturer=user)
    ).distinct()


@login_required
def course_list(request):
    user = request.user
    courses = Course.objects.select_related('lecturer').prefetch_related('enrolments')
    if user.is_admin_role():
        pass
    elif user.is_lecturer():
        courses = courses.filter(
            Q(lecturer=user) | Q(lecturer_links__lecturer=user)
        ).distinct()
    else:
        courses = courses.filter(
            is_active=True,
        ).filter(
            Q(enrolments__student=user)
            | Q(assignments__groups__memberships__student=user,
                assignments__groups__memberships__status=GroupMembership.STATUS_APPROVED)
        ).distinct()
    year_of_study = request.GET.get('year_of_study', '')
    semester = request.GET.get('semester', '')
    course_name = request.GET.get('name', '').strip()
    course_code = request.GET.get('code', '').strip()
    if year_of_study.isdecimal() and int(year_of_study) in dict(Course.YEAR_OF_STUDY_CHOICES):
        courses = courses.filter(year_of_study=int(year_of_study))
    if semester.isdecimal() and int(semester) in dict(Course.SEMESTER_CHOICES):
        courses = courses.filter(semester=int(semester))
    if course_name:
        courses = courses.filter(name__icontains=course_name)
    if course_code:
        courses = courses.filter(code__icontains=course_code)
    return render(request, 'core/course_list.html', {
        'courses': courses.order_by('code', 'name'),
        'can_manage': user.is_admin_role(),
        'years_of_study': Course.YEAR_OF_STUDY_CHOICES,
        'semesters': Course.SEMESTER_CHOICES,
        'selected_year_of_study': year_of_study,
        'selected_semester': semester,
        'selected_name': course_name,
        'selected_code': course_code,
    })


@login_required
def course_detail(request, pk):
    course = get_object_or_404(
        Course.objects.select_related('lecturer'),
        pk=pk,
    )
    user = request.user
    manages = user.is_admin_role() or (
        user.is_lecturer() and user_manages_course(user, course)
    )
    enrolled = course.enrolments.filter(student=user).exists()
    approved_group_member = GroupMembership.objects.filter(
        group__assignment__course=course,
        student=user,
        status=GroupMembership.STATUS_APPROVED,
        group__status__in=(Group.STATUS_ACTIVE, Group.STATUS_CLOSED),
    ).exists()
    if not manages and not (course.is_active and (enrolled or approved_group_member)):
        return _forbidden(request)

    assignments = course.assignments.all().prefetch_related('groups')
    if not manages:
        assignments = assignments.filter(
            status=Assignment.STATUS_PUBLISHED,
            published_at__lte=timezone.now(),
        )
    enrolments = course.enrolments.select_related('student').order_by(
        'student__last_name', 'student__first_name', 'student__username'
    ) if manages else ()
    return render(request, 'core/course_detail.html', {
        'course': course,
        'assignments': assignments.order_by('-published_at', 'deadline'),
        'enrolments': enrolments,
        'can_manage': manages,
    })


@login_required
def course_edit(request, pk):
    if not request.user.is_admin_role():
        return _forbidden(request)
    course = get_object_or_404(Course, pk=pk)
    form = CourseForm(request.POST or None, instance=course)
    if request.method == 'POST' and form.is_valid():
        course = form.save()
        log_audit(request.user, f'Updated course {course.name}')
        record_activity(request.user, 'course_updated', f'Updated course {course.name}')
        messages.success(request, 'Course updated.')
        return redirect('course_detail', course.pk)
    return render(request, 'core/course_form.html', {
        'title': f'Edit course: {course.name}',
        'form': form,
    })


@login_required
def assignment_list(request):
    user = request.user
    assignments = Assignment.objects.select_related(
        'course',
    ).prefetch_related('groups')
    if user.is_admin_role():
        pass
    elif user.is_lecturer():
        managed_courses = _managed_courses_for(user)
        assignments = assignments.filter(course__in=managed_courses)
    else:
        assignments = assignments.filter(
            course__is_active=True,
            course__enrolments__student=user,
            status=Assignment.STATUS_PUBLISHED,
            published_at__lte=timezone.now(),
        ).distinct()
    return render(request, 'core/assignment_list.html', {
        'assignments': assignments.order_by('-published_at', 'deadline'),
        'can_manage': user.is_lecturer() or user.is_admin_role(),
    })


@login_required
@require_http_methods(['GET', 'POST'])
def assignment_detail(request, pk):
    assignment = get_object_or_404(
        Assignment.objects.select_related('course__lecturer'),
        pk=pk,
    )
    user = request.user
    manages = user.is_admin_role() or (
        user.is_lecturer() and user_manages_assignment(user, assignment)
    )
    enrolled = assignment.course.enrolments.filter(student=user).exists()
    is_group_member = GroupMembership.objects.filter(
        group__assignment=assignment,
        student=user,
        status=GroupMembership.STATUS_APPROVED,
        group__status__in=(Group.STATUS_ACTIVE, Group.STATUS_CLOSED),
    ).exists()
    if not manages and not (
        assignment.course.is_active
        and assignment.is_published()
        and (enrolled or is_group_member)
    ):
        return _forbidden(request)

    proposal_form = None
    if request.method == 'POST':
        if not request.user.is_student() or not enrolled:
            return _forbidden(request)
        if not assignment.course.is_active or not assignment.is_published():
            return _forbidden(request)
        existing_membership = GroupMembership.objects.filter(
            assignment=assignment,
            student=request.user,
        ).exclude(status=GroupMembership.STATUS_REJECTED).first()

        if 'propose_group' in request.POST:
            if not assignment.allow_student_group_proposals or existing_membership:
                return _forbidden(request)
            proposal_form = GroupProposalForm(
                request.POST,
                assignment=assignment,
                proposer=request.user,
            )
            if proposal_form.is_valid():
                with transaction.atomic():
                    group = Group.objects.create(
                        assignment=assignment,
                        name=proposal_form.cleaned_data['name'],
                        description=proposal_form.cleaned_data['description'],
                        max_members=assignment.max_group_size,
                        created_by=request.user,
                        formation_type=Group.FORMATION_STUDENT,
                        status=Group.STATUS_PENDING,
                    )
                    membership = GroupMembership.objects.filter(
                        assignment=assignment,
                        student=request.user,
                        status=GroupMembership.STATUS_REJECTED,
                    ).first()
                    if membership:
                        membership.group = group
                        membership.status = GroupMembership.STATUS_PENDING
                        membership.invited_by = None
                    else:
                        membership = GroupMembership(
                            group=group,
                            assignment=assignment,
                            student=request.user,
                            status=GroupMembership.STATUS_PENDING,
                        )
                    membership.save()
                    group.leader = request.user
                    group.save()
                    for invitee in proposal_form.cleaned_data['invitees']:
                        _set_group_membership(
                            group,
                            invitee,
                            GroupMembership.STATUS_INVITED,
                            invited_by=request.user,
                        )
                        notify(
                            invitee,
                            f'You are invited to join {group.name} for {assignment.title}.',
                            Notification.TYPE_MEMBERSHIP,
                            assignment=assignment,
                            group=group,
                        )
                if assignment.course.lecturer:
                    notify(
                        assignment.course.lecturer,
                        f'{request.user.username} proposed group {group.name} for {assignment.title}.',
                        Notification.TYPE_MEMBERSHIP,
                        assignment=assignment,
                        group=group,
                    )
                record_activity(
                    request.user,
                    'group_proposed',
                    f'Proposed group {group.name}',
                    assignment=assignment,
                    group=group,
                )
                messages.success(request, 'Group proposal sent to the lecturer for approval.')
                return redirect('group_detail', group.pk)
        elif 'request_join' in request.POST:
            if existing_membership:
                return _forbidden(request)
            with transaction.atomic():
                group = get_object_or_404(
                    Group.objects.select_for_update(),
                    pk=request.POST.get('group_id'),
                    assignment=assignment,
                    status=Group.STATUS_ACTIVE,
                )
                if group.reserved_member_count() >= group.capacity:
                    messages.error(request, 'This group has reached its maximum capacity.')
                    return redirect('assignment_detail', assignment.pk)
                _set_group_membership(
                    group,
                    request.user,
                    GroupMembership.STATUS_PENDING,
                )
            if assignment.course.lecturer:
                notify(
                    assignment.course.lecturer,
                    f'{request.user.username} requested to join {group.name}.',
                    Notification.TYPE_MEMBERSHIP,
                    assignment=assignment,
                    group=group,
                )
            record_activity(
                request.user,
                'group_join_requested',
                f'Requested to join {group.name}',
                assignment=assignment,
                group=group,
            )
            messages.success(request, 'Your request to join was sent for approval.')
            return redirect('assignment_detail', assignment.pk)

    own_memberships = GroupMembership.objects.filter(
        assignment=assignment,
        student=user,
    ).exclude(status=GroupMembership.STATUS_REJECTED).select_related(
        'group',
        'group__leader',
    )
    existing_membership = own_memberships.first()
    groups = assignment.groups.select_related('leader').prefetch_related(
        'memberships__student',
        'tasks',
    ).order_by('name')
    own_memberships = GroupMembership.objects.filter(
        group__assignment=assignment,
        student=user,
    ).exclude(status=GroupMembership.STATUS_REJECTED).select_related('group')
    if not manages:
        groups = groups.filter(
            memberships__student=user,
            memberships__status__in=(
                GroupMembership.STATUS_PENDING,
                GroupMembership.STATUS_APPROVED,
                GroupMembership.STATUS_INVITED,
            ),
        ).distinct()
    available_groups = assignment.groups.filter(
        status=Group.STATUS_ACTIVE,
    ).exclude(
        memberships__assignment=assignment,
        memberships__student=user,
        memberships__status__in=(
            GroupMembership.STATUS_PENDING,
            GroupMembership.STATUS_APPROVED,
            GroupMembership.STATUS_INVITED,
        ),
    ).order_by('name')
    can_propose_group = (
        user.is_student()
        and enrolled
        and assignment.allow_student_group_proposals
        and existing_membership is None
        and assignment.is_published()
    )
    proposal_form = GroupProposalForm(
        assignment=assignment,
        proposer=user,
    ) if can_propose_group and proposal_form is None else proposal_form
    return render(request, 'core/assignment_detail.html', {
        'assignment': assignment,
        'groups': groups,
        'own_memberships': own_memberships,
        'available_groups': available_groups,
        'existing_membership': existing_membership,
        'can_propose_group': can_propose_group,
        'proposal_form': proposal_form,
        'can_manage': manages,
    })


@login_required
def assignment_edit(request, pk):
    assignment = get_object_or_404(
        Assignment.objects.select_related('course'),
        pk=pk,
    )
    if not (
        request.user.is_admin_role()
        or (
            request.user.is_lecturer()
            and user_manages_assignment(request.user, assignment)
        )
    ):
        return _forbidden(request)
    was_published = assignment.is_published()
    form = AssignmentForm(
        request.POST or None,
        instance=assignment,
        lecturer=request.user,
    )
    if request.method == 'POST' and form.is_valid():
        assignment = form.save()
        if not was_published and assignment.is_published():
            _notify_assignment_published(assignment)
        log_audit(request.user, f'Updated assignment {assignment.title}')
        record_activity(
            request.user,
            'assignment_updated',
            f'Updated assignment {assignment.title}',
            assignment=assignment,
        )
        messages.success(request, 'Assignment updated.')
        return redirect('assignment_detail', assignment.pk)
    return render(request, 'core/form.html', {
        'title': f'Manage assignment: {assignment.title}',
        'form': form,
    })


@login_required
def assignment_create(request):
    if not can_manage_academic(request.user):
        return _forbidden(request)
    form = AssignmentForm(request.POST or None, lecturer=request.user)
    if request.method == 'POST' and form.is_valid():
        course = form.cleaned_data['course']
        if not request.user.is_admin_role() and not user_manages_course(
            request.user,
            course,
        ):
            return _forbidden(request)
        assignment = form.save()
        log_audit(request.user, f'Created assignment {assignment.title}')
        record_activity(
            request.user,
            'assignment_created',
            f'Created assignment {assignment.title}',
            assignment=assignment,
        )
        if assignment.is_published():
            _notify_assignment_published(assignment)
        messages.success(request, 'Assignment created.')
        return redirect('assignment_detail', assignment.pk)
    return render(request, 'core/form.html', {'title': 'Create assignment', 'form': form})


@login_required
def group_create(request):
    return _group_create(request)


@login_required
def assignment_group_create(request, assignment_id):
    assignment = get_object_or_404(
        Assignment.objects.select_related('course'),
        pk=assignment_id,
        status=Assignment.STATUS_PUBLISHED,
        published_at__lte=timezone.now(),
        course__is_active=True,
    )
    if not (
        request.user.is_admin_role()
        or (
            request.user.is_lecturer()
            and user_manages_assignment(request.user, assignment)
        )
    ):
        return _forbidden(request)
    return _group_create(request, assignment=assignment)


def _group_create(request, assignment=None):
    if not can_manage_academic(request.user):
        return _forbidden(request)
    initial = {'assignment': assignment} if assignment else None
    form = GroupForm(
        request.POST or None,
        lecturer=request.user,
        initial=initial,
    )
    if request.method == 'POST' and form.is_valid():
        group = form.save(commit=False)
        if not user_manages_assignment(request.user, group.assignment) and not request.user.is_admin_role():
            return _forbidden(request)
        group.created_by = request.user
        group.formation_type = Group.FORMATION_LECTURER
        group.status = Group.STATUS_ACTIVE
        group.save()
        log_audit(request.user, f'Created group {group.name}')
        record_activity(
            request.user,
            'group_created',
            f'Created group {group.name}',
            assignment=group.assignment,
            group=group,
        )
        messages.success(request, 'Group created.')
        return redirect('group_detail', group.pk)
    return render(request, 'core/form.html', {'title': 'Create group', 'form': form})


@login_required
@require_http_methods(['GET', 'POST'])
def group_detail(request, pk):
    group = get_object_or_404(
        Group.objects.select_related('assignment__course'),
        pk=pk,
    )
    if not user_can_access_group(request.user, group):
        return _forbidden(request)

    can_manage = (
        user_manages_assignment(request.user, group.assignment)
        or request.user.is_admin_role()
    )
    can_manage_group = can_manage and group.status in (
        Group.STATUS_ACTIVE,
        Group.STATUS_PENDING,
    )
    own_membership = group.memberships.filter(student=request.user).first()
    can_lead = bool(
        group.leader_id == request.user.pk
        and own_membership
        and own_membership.status in (
            GroupMembership.STATUS_APPROVED,
            GroupMembership.STATUS_PENDING,
        )
    )
    can_invite = (
        group.status in (Group.STATUS_ACTIVE, Group.STATUS_PENDING)
        and (
            can_manage
            or (group.assignment.allow_student_group_proposals and can_lead)
        )
    )
    can_view_private = can_manage or user_is_group_member(request.user, group)
    membership_form = MembershipForm(group=group)
    group_form = GroupUpdateForm(instance=group, group=group)
    invitation_form = GroupInvitationForm(group=group) if can_invite else None
    message_form = GroupMessageForm(group=group)
    document_form = GroupDocumentUploadForm()
    submission_form = SubmissionForm()
    grade_form = GradeForm()

    if request.method == 'POST':
        if 'update_group' in request.POST:
            if not can_manage_group:
                return _forbidden(request)
            group_form = GroupUpdateForm(
                request.POST,
                instance=group,
                group=group,
            )
            if group_form.is_valid():
                updated_group = group_form.save()
                log_audit(request.user, f'Updated group {group.name}')
                record_activity(
                    request.user,
                    'group_updated',
                    f'Updated group {group.name}',
                    assignment=group.assignment,
                    group=group,
                )
                messages.success(request, 'Group details updated.')
                return redirect('group_detail', updated_group.pk)
        elif 'approve_group' in request.POST:
            if not can_manage_group or group.status != Group.STATUS_PENDING:
                return _forbidden(request)
            with transaction.atomic():
                group = Group.objects.select_for_update().get(pk=group.pk)
                group.status = Group.STATUS_ACTIVE
                group.save()
                pending_members = group.memberships.filter(
                    status=GroupMembership.STATUS_PENDING
                )
                for membership in pending_members:
                    membership.status = GroupMembership.STATUS_APPROVED
                    membership.save()
                    notify(
                        membership.student,
                        f'Your group {group.name} for {group.assignment.title} was approved.',
                        Notification.TYPE_MEMBERSHIP,
                        assignment=group.assignment,
                        group=group,
                    )
                if not group.leader_id:
                    first_member = group.memberships.filter(
                        status=GroupMembership.STATUS_APPROVED
                    ).order_by('joined_at', 'pk').first()
                    if first_member:
                        group.leader = first_member.student
                        group.save()
            log_audit(request.user, f'Approved group proposal {group.name}')
            record_activity(
                request.user,
                'group_approved',
                f'Approved group {group.name}',
                assignment=group.assignment,
                group=group,
            )
            messages.success(request, 'Group proposal approved.')
            return redirect('group_detail', group.pk)
        elif 'reject_group' in request.POST:
            if not can_manage_group or group.status != Group.STATUS_PENDING:
                return _forbidden(request)
            with transaction.atomic():
                group.leader = None
                group.status = Group.STATUS_REJECTED
                group.save()
                pending_members = list(group.memberships.filter(
                    status__in=(
                        GroupMembership.STATUS_PENDING,
                        GroupMembership.STATUS_INVITED,
                    )
                ).select_related('student'))
                group.memberships.filter(
                    pk__in=[membership.pk for membership in pending_members]
                ).update(status=GroupMembership.STATUS_REJECTED)
                for membership in pending_members:
                    notify(
                        membership.student,
                        f'The proposed group {group.name} was not approved.',
                        Notification.TYPE_MEMBERSHIP,
                        assignment=group.assignment,
                    )
            log_audit(request.user, f'Rejected group proposal {group.name}')
            messages.success(request, 'Group proposal rejected.')
            return redirect('assignment_detail', group.assignment_id)
        elif 'approve_member' in request.POST:
            if not can_manage_group or group.status != Group.STATUS_ACTIVE:
                return _forbidden(request)
            with transaction.atomic():
                group = Group.objects.select_for_update().get(pk=group.pk)
                membership = get_object_or_404(
                    GroupMembership.objects.select_for_update(),
                    pk=request.POST.get('membership_id'),
                    group=group,
                    status=GroupMembership.STATUS_PENDING,
                )
                membership.status = GroupMembership.STATUS_APPROVED
                membership.save()
                if not group.leader_id:
                    group.leader = membership.student
                    group.save()
            notify(
                membership.student,
                f'Your membership in {group.name} was approved.',
                Notification.TYPE_MEMBERSHIP,
                assignment=group.assignment,
                group=group,
            )
            log_audit(request.user, f'Approved {membership.student.username} for {group.name}')
            messages.success(request, 'Membership request approved.')
            return redirect('group_detail', group.pk)
        elif 'reject_member' in request.POST:
            if not can_manage_group:
                return _forbidden(request)
            membership = get_object_or_404(
                GroupMembership,
                pk=request.POST.get('membership_id'),
                group=group,
                status__in=(
                    GroupMembership.STATUS_PENDING,
                    GroupMembership.STATUS_INVITED,
                ),
            )
            membership.status = GroupMembership.STATUS_REJECTED
            membership.save()
            notify(
                membership.student,
                f'Your request to join {group.name} was not approved.',
                Notification.TYPE_MEMBERSHIP,
                assignment=group.assignment,
            )
            log_audit(request.user, f'Rejected {membership.student.username} for {group.name}')
            messages.success(request, 'Membership request rejected.')
            return redirect('group_detail', group.pk)
        elif 'remove_member' in request.POST:
            if not can_manage_group:
                return _forbidden(request)
            with transaction.atomic():
                group = Group.objects.select_for_update().get(pk=group.pk)
                membership = get_object_or_404(
                    GroupMembership.objects.select_for_update(),
                    pk=request.POST.get('membership_id'),
                    group=group,
                    status=GroupMembership.STATUS_APPROVED,
                )
                removed_student = membership.student
                if group.leader_id == removed_student.pk:
                    next_leader = group.memberships.filter(
                        status=GroupMembership.STATUS_APPROVED
                    ).exclude(pk=membership.pk).order_by('joined_at', 'pk').first()
                    group.leader = next_leader.student if next_leader else None
                    group.save()
                membership.delete()
            notify(
                removed_student,
                f'You were removed from {group.name}.',
                Notification.TYPE_MEMBERSHIP,
                assignment=group.assignment,
            )
            log_audit(request.user, f'Removed {removed_student.username} from {group.name}')
            messages.success(request, 'Student removed from the group.')
            return redirect('group_detail', group.pk)
        elif 'invite_students' in request.POST:
            if not can_invite:
                return _forbidden(request)
            invitation_form = GroupInvitationForm(request.POST, group=group)
            if invitation_form.is_valid():
                try:
                    with transaction.atomic():
                        group = Group.objects.select_for_update().get(pk=group.pk)
                        if (
                            group.reserved_member_count()
                            + invitation_form.cleaned_data['students'].count()
                            > group.capacity
                        ):
                            raise ValidationError(
                                'Invitations would exceed this group’s capacity.'
                            )
                        for invited_student in invitation_form.cleaned_data['students']:
                            _set_group_membership(
                                group,
                                invited_student,
                                GroupMembership.STATUS_INVITED,
                                invited_by=request.user,
                            )
                except ValidationError as error:
                    invitation_form.add_error('students', error)
                else:
                    for invited_student in invitation_form.cleaned_data['students']:
                        notify(
                            invited_student,
                            f'You are invited to join {group.name} for {group.assignment.title}.',
                            Notification.TYPE_MEMBERSHIP,
                            assignment=group.assignment,
                            group=group,
                        )
                    record_activity(
                        request.user,
                        'group_invites_sent',
                        f'Invited students to {group.name}',
                        assignment=group.assignment,
                        group=group,
                    )
                    messages.success(request, 'Invitations sent for lecturer approval.')
                    return redirect('group_detail', group.pk)
        elif 'accept_invitation' in request.POST:
            if not own_membership or own_membership.status != GroupMembership.STATUS_INVITED:
                return _forbidden(request)
            own_membership.status = GroupMembership.STATUS_PENDING
            own_membership.invited_by = None
            own_membership.save()
            if group.assignment.course.lecturer:
                notify(
                    group.assignment.course.lecturer,
                    f'{request.user.username} accepted an invitation to {group.name}.',
                    Notification.TYPE_MEMBERSHIP,
                )
            messages.success(request, 'Invitation accepted and sent for lecturer approval.')
            return redirect('assignment_detail', group.assignment_id)
        elif 'decline_invitation' in request.POST:
            if not own_membership or own_membership.status != GroupMembership.STATUS_INVITED:
                return _forbidden(request)
            own_membership.status = GroupMembership.STATUS_REJECTED
            own_membership.save()
            messages.success(request, 'Invitation declined.')
            return redirect('assignment_detail', group.assignment_id)
        elif 'add_member' in request.POST or (
            'student' in request.POST
            and can_manage
            and 'body' not in request.POST
            and 'file' not in request.POST
            and 'score' not in request.POST
        ):
            if not can_manage_group:
                return _forbidden(request)
            membership_form = MembershipForm(request.POST, group=group)
            if membership_form.is_valid():
                try:
                    with transaction.atomic():
                        group = Group.objects.select_for_update().get(pk=group.pk)
                        membership_form.group = group
                        membership = membership_form.save()
                        if not group.leader_id and membership.status == GroupMembership.STATUS_APPROVED:
                            group.leader = membership.student
                            group.save()
                except ValidationError as error:
                    membership_form.add_error('student', error)
                else:
                    notify(
                        membership.student,
                        f'You were added to group {group.name} for {group.assignment.title}.',
                        Notification.TYPE_MEMBERSHIP,
                        assignment=group.assignment,
                        group=group,
                    )
                    record_activity(
                        request.user,
                        'membership_added',
                        f'Added {membership.student.username} to {group.name}',
                        assignment=group.assignment,
                        group=group,
                    )
                    log_audit(request.user, f'Added {membership.student.username} to {group.name}')
                    messages.success(
                        request,
                        'Student added to the group.'
                        if membership.status == GroupMembership.STATUS_APPROVED
                        else 'Student added pending group approval.',
                    )
                    return redirect('group_detail', group.pk)
        elif 'upload_document' in request.POST:
            if not can_view_private or not user_is_group_member(request.user, group):
                return _forbidden(request)
            document_form = GroupDocumentUploadForm(request.POST, request.FILES)
            if document_form.is_valid():
                uploaded_file = document_form.cleaned_data['document']
                original_filename = get_valid_filename(
                    uploaded_file.name.replace('\\', '/').rsplit('/', 1)[-1]
                )[:255]
                with transaction.atomic():
                    locked_group = Group.objects.select_for_update().get(pk=group.pk)
                    previous_version = FileDocument.objects.filter(
                        group=locked_group,
                        document_type=FileDocument.TYPE_GROUP_FILE,
                        original_filename__iexact=original_filename,
                    ).order_by('-version').values_list('version', flat=True).first()
                    document = FileDocument.objects.create(
                        group=locked_group,
                        uploaded_by=request.user,
                        file=uploaded_file,
                        original_filename=original_filename,
                        file_type=getattr(uploaded_file, 'content_type', '') or '',
                        file_size=uploaded_file.size,
                        version=(previous_version or 0) + 1,
                        document_type=FileDocument.TYPE_GROUP_FILE,
                    )
                record_activity(
                    request.user,
                    'group_document_uploaded',
                    f'Uploaded {original_filename} (version {document.version})',
                    assignment=group.assignment,
                    group=group,
                    document=document,
                )
                for membership in group.memberships.filter(
                    status=GroupMembership.STATUS_APPROVED,
                ).exclude(student=request.user):
                    notify(
                        membership.student,
                        f'New document uploaded to {group.name}: {original_filename}.',
                        Notification.TYPE_DOCUMENT,
                        assignment=group.assignment,
                        group=group,
                    )
                log_audit(request.user, f'Uploaded {original_filename} to {group.name}')
                messages.success(request, 'Document uploaded.')
                return redirect('group_detail', group.pk)
        elif 'post_message' in request.POST:
            if not user_is_group_member(request.user, group) and not can_manage:
                return _forbidden(request)
            message_form = GroupMessageForm(request.POST, group=group)
            if message_form.is_valid():
                msg = message_form.save(commit=False)
                msg.group = group
                msg.author = request.user
                msg.save()
                record_activity(
                    request.user,
                    'group_message',
                    f'Posted a message in {group.name}',
                    assignment=group.assignment,
                    group=group,
                    task=msg.task,
                )
                log_audit(request.user, f'Posted a message in {group.name}')
                for membership in group.memberships.filter(status=GroupMembership.STATUS_APPROVED).exclude(
                    student=request.user
                ):
                    notify(
                        membership.student,
                        f'New message in {group.name} from {request.user.username}.',
                        Notification.TYPE_MESSAGE,
                        assignment=group.assignment,
                        group=group,
                    )
                messages.success(request, 'Message posted.')
                return redirect('group_detail', group.pk)
        elif 'submit_assignment' in request.POST:
            if not user_is_group_member(request.user, group):
                return _forbidden(request)
            submission_form = SubmissionForm(request.POST, request.FILES)
            if submission_form.is_valid():
                submission = submission_form.save(commit=False)
                submission.group = group
                submission.submitted_by = request.user
                submission.original_filename = submission.file.name
                submission.is_latest = False
                was_late = timezone.now() > group.assignment.deadline
                if was_late:
                    if not group.assignment.allow_late_submissions:
                        messages.error(request, 'Late submissions are not allowed for this assignment.')
                        return redirect('group_detail', group.pk)
                    submission.status = Submission.STATUS_LATE
                submission.full_clean()
                with transaction.atomic():
                    Submission.objects.filter(
                        group=group,
                        is_latest=True,
                    ).update(is_latest=False)
                    submission.is_latest = True
                    submission.save()
                record_activity(
                    request.user,
                    'submission',
                    f'Submitted {group.assignment.title} for {group.name}',
                    assignment=group.assignment,
                    group=group,
                    submission=submission,
                    contributor=submission.submitted_by,
                )
                notify(
                    submission.submitted_by,
                    f'Submission recorded for {group.assignment.title}.',
                    Notification.TYPE_SUBMISSION,
                    assignment=group.assignment,
                    group=group,
                    deduplication_key=(
                        f'submission-received:{submission.pk}:user:{submission.submitted_by_id}'
                    ),
                )
                submission_recipients = set(
                    group.assignment.course.lecturer_links.values_list('lecturer', flat=True)
                )
                if group.assignment.course.lecturer_id:
                    submission_recipients.add(group.assignment.course.lecturer)
                for lecturer in submission_recipients:
                    notify(
                        lecturer,
                        f'{group.name} submitted {group.assignment.title}.',
                        Notification.TYPE_SUBMISSION,
                        assignment=group.assignment,
                        group=group,
                    )
                messages.success(request, 'Submission recorded successfully.')
                return redirect('submission_receipt', submission.pk)
        elif 'save_grade' in request.POST:
            if not can_manage:
                return _forbidden(request)
            latest = group.submissions.filter(is_latest=True).first()
            if not latest:
                messages.error(request, 'No submission available to grade.')
                return redirect('group_detail', group.pk)
            grade_form = GradeForm(request.POST)
            if grade_form.is_valid():
                data = grade_form.cleaned_data
                was_released = Grade.objects.filter(
                    submission=latest,
                    is_released=True,
                ).exists()
                grade, _created = Grade.objects.update_or_create(
                    submission=latest,
                    defaults={
                        'graded_by': request.user,
                        'score': data['score'],
                        'max_score': data['max_score'],
                        'feedback': data['feedback'],
                        'is_released': data['release_now'],
                        'released_at': timezone.now() if data['release_now'] else None,
                    },
                )
                latest.status = (
                    Submission.STATUS_FEEDBACK_PUBLISHED
                    if data['release_now']
                    else Submission.STATUS_UNDER_REVIEW
                )
                latest.save(update_fields=['status'])
                if data['release_now'] and not was_released:
                    for membership in group.memberships.filter(status=GroupMembership.STATUS_APPROVED):
                        notify(
                            membership.student,
                            f'Feedback released for {group.assignment.title}.',
                            Notification.TYPE_FEEDBACK,
                            assignment=group.assignment,
                            group=group,
                        )
                log_audit(request.user, f'Graded submission for {group.name}')
                messages.success(request, 'Grade saved.')
                return redirect('group_detail', group.pk)

    members = User.objects.filter(
        group_memberships__group=group,
        group_memberships__status=GroupMembership.STATUS_APPROVED,
    ).distinct() if can_view_private else ()
    member_rows = group.memberships.filter(
        status=GroupMembership.STATUS_APPROVED
    ).select_related('student') if can_view_private else ()
    pending_memberships = group.memberships.filter(
        status__in=(GroupMembership.STATUS_PENDING, GroupMembership.STATUS_INVITED)
    ).select_related('student')
    tasks = group.tasks.select_related('assigned_to', 'internal_deadline').order_by(
        'internal_deadline__due_at'
    ) if can_view_private else ()
    group_messages = group.messages.select_related('author').all() if can_view_private else ()
    group_documents = group.files.filter(
        document_type=FileDocument.TYPE_GROUP_FILE,
    ).select_related('uploaded_by').order_by('original_filename', '-version', '-uploaded_at') if can_view_private else ()
    group_activity_history = group.activity_logs.select_related('user').all()[:50] if can_view_private else ()
    latest_submission = (
        group.submissions.filter(is_latest=True).select_related('grade').first()
        if can_view_private
        else None
    )
    submission_history = (
        group.submissions.select_related('submitted_by', 'grade').all()
        if can_view_private
        else ()
    )

    return render(
        request,
        'core/group_detail.html',
        {
            'group': group,
            'members': members,
            'member_rows': member_rows,
            'tasks': tasks,
            'can_manage': can_manage,
            'can_manage_group': can_manage_group,
            'can_lead': can_lead,
            'can_invite': can_invite,
            'can_view_private': can_view_private,
            'membership_form': membership_form,
            'group_form': group_form,
            'invitation_form': invitation_form,
            'pending_memberships': pending_memberships,
            'own_membership': own_membership,
            'message_form': message_form,
            'document_form': document_form,
            'submission_form': submission_form,
            'grade_form': grade_form,
            'group_messages': group_messages,
            'group_documents': group_documents,
            'group_activity_history': group_activity_history,
            'latest_submission': latest_submission,
            'submission_history': submission_history,
            'progress_percent': group.task_completion_percent() if can_view_private else 0,
        },
    )


@login_required
def group_document_download(request, pk):
    document = get_object_or_404(
        FileDocument.objects.select_related('group__assignment__course'),
        pk=pk,
        document_type=FileDocument.TYPE_GROUP_FILE,
    )
    if not user_can_access_group_content(request.user, document.group):
        return _forbidden(request)
    response = FileResponse(
        document.file.open('rb'),
        as_attachment=True,
        filename=document.original_filename or 'group-document',
        content_type='application/octet-stream',
    )
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@login_required
@require_http_methods(['POST'])
def group_document_delete(request, pk):
    document = get_object_or_404(
        FileDocument.objects.select_related(
            'group__assignment__course',
            'uploaded_by',
        ),
        pk=pk,
        document_type=FileDocument.TYPE_GROUP_FILE,
    )
    if not user_can_access_group_content(request.user, document.group):
        return _forbidden(request)
    can_manage = (
        user_manages_assignment(request.user, document.group.assignment)
        or request.user.is_admin_role()
    )
    if document.uploaded_by_id != request.user.pk and not can_manage:
        return _forbidden(request)
    group = document.group
    filename = document.original_filename or document.file.name
    record_activity(
        request.user,
        'group_document_deleted',
        f'Deleted {filename} (version {document.version})',
        assignment=group.assignment,
        group=group,
    )
    log_audit(request.user, f'Deleted {filename} from {group.name}')
    document.file.delete(save=False)
    document.delete()
    messages.success(request, 'Document deleted.')
    return redirect('group_detail', group.pk)


@login_required
def task_create(request, group_id):
    group = get_object_or_404(Group, pk=group_id)
    if not user_can_access_group_content(request.user, group):
        return _forbidden(request)
    if not (
        user_is_group_member(request.user, group)
        or user_manages_assignment(request.user, group.assignment)
        or request.user.is_admin_role()
    ):
        return _forbidden(request)

    form = TaskForm(request.POST or None, group=group)
    if request.method == 'POST' and form.is_valid():
        task = form.save(commit=False)
        task.group = group
        task.created_by = request.user
        if not task.title:
            task.title = task.description[:200]
        task.save()
        log_audit(request.user, f'Created task {task.title} for {group.name}')
        notify(
            task.assigned_to,
            f'You were assigned a task in {group.name}: {task.title or task.description[:80]}',
            Notification.TYPE_TASK,
            assignment=group.assignment,
            group=group,
            task=task,
        )
        record_activity(
            request.user,
            'task_created',
            f'Created task for {group.name}',
            assignment=group.assignment,
            group=group,
            task=task,
        )
        record_activity(
            request.user,
            'task_assigned',
            f'Assigned task {task.title or task.description[:80]}',
            assignment=group.assignment,
            group=group,
            task=task,
            contributor=task.assigned_to,
        )
        messages.success(request, 'Task created.')
        return redirect('task_detail', task.pk)
    return render(request, 'core/form.html', {'title': 'Create task', 'form': form})


@login_required
@require_http_methods(['GET', 'POST'])
def task_detail(request, pk):
    task = get_object_or_404(
        Task.objects.select_related(
            'group__assignment__course',
            'assigned_to',
            'internal_deadline',
        ),
        pk=pk,
    )
    if not user_can_access_group_content(request.user, task.group):
        return _forbidden(request)

    can_modify_task = (
        user_is_group_member(request.user, task.group)
        or user_manages_assignment(request.user, task.group.assignment)
        or request.user.is_admin_role()
    )
    can_change_status = can_modify_task
    task_form = TaskForm(instance=task, group=task.group)
    status_form = TaskStatusForm(initial={'status': task.status})
    update_form = TaskUpdateForm()
    evidence_form = EvidenceUploadForm()

    if request.method == 'POST':
        if 'update_task' in request.POST:
            if not can_modify_task:
                return _forbidden(request)
            task_form = TaskForm(request.POST, instance=task, group=task.group)
            if task_form.is_valid():
                previous_assignee = task.assigned_to
                updated_task = task_form.save(commit=False)
                updated_task.save()
                if previous_assignee.pk != updated_task.assigned_to_id:
                    notify(
                        updated_task.assigned_to,
                        f'You were assigned a task in {task.group.name}: '
                        f'{updated_task.title or updated_task.description[:80]}',
                        Notification.TYPE_TASK,
                        assignment=task.group.assignment,
                        group=task.group,
                        task=updated_task,
                    )
                record_activity(
                    request.user,
                    'task_updated',
                    f'Updated task {updated_task.title or updated_task.description[:80]}',
                    assignment=task.group.assignment,
                    group=task.group,
                    task=task,
                )
                if previous_assignee.pk != updated_task.assigned_to_id:
                    record_activity(
                        request.user,
                        'task_assigned',
                        f'Assigned task {updated_task.title or updated_task.description[:80]}',
                        assignment=task.group.assignment,
                        group=task.group,
                        task=updated_task,
                        contributor=updated_task.assigned_to,
                    )
                log_audit(request.user, f'Updated task {task.pk} for {task.group.name}')
                messages.success(request, 'Task details updated.')
                return redirect('task_detail', task.pk)
        if 'change_status' in request.POST:
            if not can_modify_task:
                return _forbidden(request)
            status_form = TaskStatusForm(request.POST)
            if status_form.is_valid():
                old_status = task.status
                task.status = status_form.cleaned_data['status']
                task.save()
                if old_status != task.status:
                    record_activity(
                        request.user,
                        'task_status',
                        f'Changed task status to {task.effective_status_display}',
                        assignment=task.group.assignment,
                        group=task.group,
                        task=task,
                    )
                    log_audit(request.user, f'Changed task {task.pk} status')
                    messages.success(request, 'Task status updated.')
                    return redirect('task_detail', task.pk)
        if 'add_update' in request.POST:
            if not can_modify_task:
                return _forbidden(request)
            update_form = TaskUpdateForm(request.POST)
            if update_form.is_valid():
                update = update_form.save(commit=False)
                update.task = task
                update.author = request.user
                update.save()
                record_activity(
                    request.user,
                    'task_update',
                    'Posted a task update',
                    assignment=task.group.assignment,
                    group=task.group,
                    task=task,
                )
                log_audit(request.user, f'Added progress update to task {task.pk}')
                messages.success(request, 'Update posted.')
                return redirect('task_detail', task.pk)
        if 'upload_evidence' in request.POST:
            if not can_modify_task:
                return _forbidden(request)
            evidence_form = EvidenceUploadForm(request.POST, request.FILES)
            if evidence_form.is_valid():
                uploaded = evidence_form.cleaned_data['evidence_file']
                evidence = FileDocument.objects.create(
                    task=task,
                    group=task.group,
                    uploaded_by=request.user,
                    file=uploaded,
                    original_filename=uploaded.name,
                    file_type=getattr(uploaded, 'content_type', '') or '',
                    file_size=uploaded.size,
                    document_type=FileDocument.TYPE_CONTRIBUTION_EVIDENCE,
                )
                record_activity(
                    request.user,
                    'evidence_upload',
                    f'Uploaded evidence {evidence.original_filename}',
                    assignment=task.group.assignment,
                    group=task.group,
                    task=task,
                    document=evidence,
                )
                messages.success(request, 'Evidence uploaded.')
                return redirect('task_detail', task.pk)

    return render(
        request,
        'core/task_detail.html',
        {
            'task': task,
            'task_form': task_form,
            'status_form': status_form,
            'can_modify_task': can_modify_task,
            'can_change_status': can_change_status,
            'update_form': update_form,
            'evidence_form': evidence_form,
            'updates': task.updates.all(),
            'evidence': task.evidence_items.select_related('uploaded_by'),
            'activity_history': task.activity_logs.select_related('user')[:20],
        },
    )


@login_required
def evidence_download(request, pk):
    evidence = get_object_or_404(
        FileDocument.objects.select_related('task__group'),
        pk=pk,
        document_type=FileDocument.TYPE_CONTRIBUTION_EVIDENCE,
    )
    if not user_can_access_group_content(request.user, evidence.task.group):
        return _forbidden(request)
    return FileResponse(
        evidence.file.open('rb'),
        as_attachment=True,
        filename=evidence.original_filename or evidence.file.name,
    )


@login_required
def peer_review_create(request, assignment_id):
    assignment = get_object_or_404(Assignment, pk=assignment_id)
    if request.user.role != User.ROLE_STUDENT:
        return _forbidden(request)
    member_groups = Group.objects.filter(
        assignment=assignment,
        memberships__student=request.user,
        memberships__status=GroupMembership.STATUS_APPROVED,
        status__in=(Group.STATUS_ACTIVE, Group.STATUS_CLOSED),
    )
    if not member_groups.exists():
        return _forbidden(request)
    if not assignment.peer_evaluation_enabled:
        messages.error(request, 'Peer evaluation is not enabled for this assignment.')
        return redirect('dashboard')

    form = PeerReviewForm(request.POST or None, assignment=assignment, evaluator=request.user)
    if request.method == 'POST' and form.is_valid():
        review = form.save(commit=False)
        review.assignment = assignment
        review.evaluator = request.user
        review.save()
        record_activity(
            request.user,
            'peer_evaluation_submitted',
            f'Submitted a confidential peer evaluation for {assignment.title}',
            assignment=assignment,
        )
        messages.success(request, 'Peer review submitted.')
        return redirect('dashboard')
    return render(request, 'core/form.html', {
        'title': 'Confidential peer evaluation',
        'form': form,
        'extra_note': (
            'Your evaluation is confidential and will only be available to authorised '
            'lecturers. Scores support lecturer judgement and do not determine grades.'
        ),
    })


@login_required
def peer_evaluation_report(request, assignment_id):
    assignment = get_object_or_404(
        Assignment.objects.select_related('course'),
        pk=assignment_id,
    )
    if not (
        request.user.is_admin_role()
        or request.user.is_superuser
        or (
            request.user.is_lecturer()
            and user_manages_assignment(request.user, assignment)
        )
    ):
        return _forbidden(request)

    criteria_labels = dict(Assignment.PEER_EVALUATION_CRITERIA)
    review_rows = PeerReview.objects.filter(
        assignment=assignment,
    ).select_related('evaluator', 'evaluated').order_by(
        'evaluated__last_name', 'evaluated__first_name', 'created_at',
    )
    reviews_by_member = {}
    for review in review_rows:
        key = review.evaluated_id
        if key not in reviews_by_member:
            reviews_by_member[key] = {
                'student': review.evaluated,
                'group': GroupMembership.objects.filter(
                    assignment=assignment,
                    student=review.evaluated,
                    status=GroupMembership.STATUS_APPROVED,
                ).select_related('group').first(),
                'reviews': [],
                'criterion_totals': {},
                'criterion_counts': {},
            }
        member_row = reviews_by_member[key]
        member_row['reviews'].append(review)
        for criterion, score in review.criteria_scores.items():
            member_row['criterion_totals'][criterion] = (
                member_row['criterion_totals'].get(criterion, 0) + score
            )
            member_row['criterion_counts'][criterion] = (
                member_row['criterion_counts'].get(criterion, 0) + 1
            )

    summaries = []
    for member_row in reviews_by_member.values():
        member_row['criteria'] = [
            {
                'key': criterion,
                'label': criteria_labels.get(
                    criterion,
                    criterion.replace('_', ' ').title(),
                ),
                'average': (
                    member_row['criterion_totals'][criterion]
                    / member_row['criterion_counts'][criterion]
                ),
                'count': member_row['criterion_counts'][criterion],
            }
            for criterion in assignment.peer_evaluation_criteria
            if member_row['criterion_counts'].get(criterion)
        ]
        summaries.append(member_row)

    return render(request, 'core/peer_evaluation_report.html', {
        'assignment': assignment,
        'summaries': summaries,
        'evaluation_count': review_rows.count(),
    })


@login_required
@require_http_methods(['GET', 'POST'])
def notifications(request):
    qs = Notification.objects.filter(user=request.user).select_related(
        'assignment', 'group', 'task',
    )
    if request.method == 'POST':
        if 'mark_all_read' in request.POST:
            qs.filter(read=False).update(read=True)
            return redirect('notifications')
        notification_id = request.POST.get('notification_id')
        notification = get_object_or_404(Notification, pk=notification_id, user=request.user)
        notification.read = True
        notification.save(update_fields=['read'])
        return redirect('notifications')
    page_obj = Paginator(qs, 25).get_page(request.GET.get('page'))
    return render(request, 'core/notifications.html', {
        'notifications': page_obj.object_list,
        'page_obj': page_obj,
    })


@login_required
def submission_list(request):
    user = request.user
    if user.is_admin_role() or user.is_superuser:
        submissions = Submission.objects.all()
        courses = Course.objects.all()
        groups = Group.objects.filter(
            status__in=(Group.STATUS_ACTIVE, Group.STATUS_CLOSED),
        )
    elif user.is_lecturer():
        courses = Course.objects.filter(
            Q(lecturer=user) | Q(lecturer_links__lecturer=user)
        ).distinct()
        submissions = Submission.objects.filter(
            group__assignment__course__in=courses,
        ).distinct()
        groups = Group.objects.filter(
            assignment__course__in=courses,
            status__in=(Group.STATUS_ACTIVE, Group.STATUS_CLOSED),
        ).distinct()
    elif user.is_student():
        courses = Course.objects.filter(
            enrolments__student=user,
        ).distinct()
        submissions = Submission.objects.filter(
            group__memberships__student=user,
            group__memberships__status=GroupMembership.STATUS_APPROVED,
            group__status__in=(Group.STATUS_ACTIVE, Group.STATUS_CLOSED),
        ).distinct()
        groups = Group.objects.filter(
            memberships__student=user,
            memberships__status=GroupMembership.STATUS_APPROVED,
            status__in=(Group.STATUS_ACTIVE, Group.STATUS_CLOSED),
        ).distinct()
    else:
        return _forbidden(request)

    course_id = request.GET.get('course')
    if course_id and course_id.isdecimal():
        submissions = submissions.filter(group__assignment__course_id=course_id)
        groups = groups.filter(assignment__course_id=course_id)
    assignment_id = request.GET.get('assignment')
    if assignment_id and assignment_id.isdecimal():
        submissions = submissions.filter(group__assignment_id=assignment_id)
        groups = groups.filter(assignment_id=assignment_id)
    assignment_options = Assignment.objects.filter(
        pk__in=submissions.values_list('group__assignment_id', flat=True)
    ).order_by('title')
    submissions = submissions.select_related(
        'group__assignment__course',
        'group',
        'submitted_by',
        'grade',
    ).order_by('-submitted_at')
    paginator = Paginator(submissions, 25)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'core/submission_list.html', {
        'page_obj': page_obj,
        'submissions': page_obj.object_list,
        'pending_groups': groups.filter(
            submissions__isnull=True,
        ).select_related('assignment__course').order_by(
            'assignment__deadline', 'name',
        ),
        'courses': courses.order_by('code', 'name'),
        'assignment_options': assignment_options,
        'selected_course': course_id or '',
        'selected_assignment': assignment_id or '',
        'is_lecturer_view': user.is_lecturer() or user.is_admin_role() or user.is_superuser,
    })


@login_required
def submission_receipt(request, pk):
    submission = get_object_or_404(
        Submission.objects.select_related(
            'group__assignment__course',
            'submitted_by',
            'grade',
        ),
        pk=pk,
    )
    if not user_can_access_group_content(request.user, submission.group):
        return _forbidden(request)
    can_review = (
        request.user.is_admin_role()
        or request.user.is_superuser
        or user_manages_assignment(request.user, submission.assignment)
    )
    return render(request, 'core/submission_receipt.html', {
        'submission': submission,
        'can_review': can_review,
        'show_feedback': can_review or (
            hasattr(submission, 'grade') and submission.grade.is_released
        ),
    })


@login_required
def submission_download(request, pk):
    submission = get_object_or_404(
        Submission.objects.select_related('group__assignment'),
        pk=pk,
    )
    if not user_can_access_group_content(request.user, submission.group):
        return _forbidden(request)
    return FileResponse(
        submission.file.open('rb'),
        as_attachment=True,
        filename=submission.original_filename or submission.file.name,
    )


@login_required
def profile(request):
    return render(request, 'core/profile.html', {'profile_user': request.user})


def _forbidden(request):
    return render(request, 'core/forbidden.html', status=403)


# --- Optional authenticated API (not used by the primary Bootstrap UI) ---


class UserViewSet(viewsets.ModelViewSet):
    serializer_class = AdminUserSerializer
    permission_classes = [SystemAdministratorPermission]
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def get_queryset(self):
        return User.objects.select_related(
            'student_profile',
            'lecturer_profile',
            'administrator_profile',
        ).order_by('username')


class CourseViewSet(viewsets.ModelViewSet):
    serializer_class = CourseSerializer
    permission_classes = [ScopedRolePermission]
    write_roles = (User.ROLE_ADMIN,)

    def get_queryset(self):
        user = self.request.user
        if user.is_admin_role():
            courses = Course.objects.all()
        elif user.is_lecturer():
            courses = Course.objects.filter(
                Q(lecturer=user) | Q(lecturer_links__lecturer=user)
            ).distinct()
        else:
            courses = Course.objects.filter(
                is_active=True,
                enrolments__student=user,
            ).distinct()

        year_of_study = self.request.query_params.get('year_of_study')
        semester_id = self.request.query_params.get('semester')
        course_name = self.request.query_params.get('name')
        course_code = self.request.query_params.get('code')
        if year_of_study and (
            not year_of_study.isdecimal()
            or int(year_of_study) not in dict(Course.YEAR_OF_STUDY_CHOICES)
        ):
            raise APIValidationError({
                'year_of_study': 'Use a year of study between 1 and 4.',
            })
        if semester_id and (
            not semester_id.isdecimal()
            or int(semester_id) not in dict(Course.SEMESTER_CHOICES)
        ):
            raise APIValidationError({'semester': 'Use a semester of 1 or 2.'})
        if year_of_study:
            courses = courses.filter(year_of_study=int(year_of_study))
        if semester_id:
            courses = courses.filter(semester=int(semester_id))
        if course_name:
            courses = courses.filter(name__icontains=course_name)
        if course_code:
            courses = courses.filter(code__icontains=course_code)
        return courses


class AssignmentViewSet(viewsets.ModelViewSet):
    serializer_class = AssignmentSerializer
    permission_classes = [ScopedRolePermission]

    def get_queryset(self):
        user = self.request.user
        if user.is_admin_role():
            return Assignment.objects.all()
        if user.is_lecturer():
            return Assignment.objects.filter(
                Q(course__lecturer=user) | Q(course__lecturer_links__lecturer=user)
            ).distinct()
        return (
            Assignment.objects.filter(
                status=Assignment.STATUS_PUBLISHED,
                published_at__lte=timezone.now(),
                course__is_active=True,
                course__enrolments__student=user,
            )
            | Assignment.objects.filter(
                status=Assignment.STATUS_PUBLISHED,
                published_at__lte=timezone.now(),
                course__is_active=True,
                groups__memberships__student=user,
                groups__memberships__status=GroupMembership.STATUS_APPROVED,
                groups__status__in=(Group.STATUS_ACTIVE, Group.STATUS_CLOSED),
            )
        ).distinct()

    def perform_create(self, serializer):
        assignment = serializer.save()
        _notify_assignment_published(assignment)

    def perform_update(self, serializer):
        was_published = serializer.instance.is_published()
        assignment = serializer.save()
        if not was_published and assignment.is_published():
            _notify_assignment_published(assignment)


class GroupViewSet(viewsets.ModelViewSet):
    serializer_class = GroupSerializer
    permission_classes = [ScopedRolePermission]

    def get_queryset(self):
        user = self.request.user
        if user.is_admin_role():
            groups = Group.objects.all()
        elif user.is_lecturer():
            groups = Group.objects.filter(
                Q(assignment__course__lecturer=user)
                | Q(assignment__course__lecturer_links__lecturer=user)
            ).distinct()
        else:
            groups = Group.objects.filter(
                memberships__student=user,
                memberships__status=GroupMembership.STATUS_APPROVED,
                status__in=(Group.STATUS_ACTIVE, Group.STATUS_CLOSED),
            ).distinct()
        return groups.select_related('assignment__course').prefetch_related(
            'memberships__student',
            'tasks',
        )


class TaskViewSet(viewsets.ModelViewSet):
    serializer_class = TaskSerializer
    permission_classes = [TaskManagementPermission]
    student_write_actions = ('create', 'update', 'partial_update')

    def get_queryset(self):
        user = self.request.user
        if user.is_admin_role():
            tasks = Task.objects.all()
        elif user.is_lecturer():
            tasks = Task.objects.filter(
                Q(group__assignment__course__lecturer=user)
                | Q(group__assignment__course__lecturer_links__lecturer=user)
            ).distinct()
        else:
            tasks = Task.objects.filter(
                group__memberships__student=user,
                group__memberships__status=GroupMembership.STATUS_APPROVED,
                group__status__in=(Group.STATUS_ACTIVE, Group.STATUS_CLOSED),
            ).distinct()
        return tasks.select_related(
            'group__assignment__course',
            'assigned_to',
            'created_by',
            'internal_deadline',
        )

    def perform_create(self, serializer):
        task = serializer.save(created_by=self.request.user)
        notify(
            task.assigned_to,
            f'You were assigned a task in {task.group.name}: '
            f'{task.title or task.description[:80]}',
            Notification.TYPE_TASK,
            task=task,
        )
        record_activity(
            self.request.user,
            'task_created',
            f'Created task {task.title or task.description[:80]}',
            assignment=task.group.assignment,
            group=task.group,
            task=task,
        )
        record_activity(
            self.request.user,
            'task_assigned',
            f'Assigned task {task.title or task.description[:80]}',
            assignment=task.group.assignment,
            group=task.group,
            task=task,
            contributor=task.assigned_to,
        )
        log_audit(self.request.user, f'Created task {task.pk}')

    def perform_update(self, serializer):
        previous_assignee_id = serializer.instance.assigned_to_id
        previous_status = serializer.instance.status
        task = serializer.save()
        if previous_assignee_id != task.assigned_to_id:
            notify(
                task.assigned_to,
                f'You were assigned a task in {task.group.name}: '
                f'{task.title or task.description[:80]}',
                Notification.TYPE_TASK,
                task=task,
            )
        record_activity(
            self.request.user,
            'task_updated',
            f'Updated task {task.title or task.description[:80]}',
            assignment=task.group.assignment,
            group=task.group,
            task=task,
        )
        if previous_assignee_id != task.assigned_to_id:
            record_activity(
                self.request.user,
                'task_assigned',
                f'Assigned task {task.title or task.description[:80]}',
                assignment=task.group.assignment,
                group=task.group,
                task=task,
                contributor=task.assigned_to,
            )
        if previous_status != task.status:
            record_activity(
                self.request.user,
                'task_status',
                f'Changed task status to {task.effective_status_display}',
                assignment=task.group.assignment,
                group=task.group,
                task=task,
            )
        log_audit(self.request.user, f'Updated task {task.pk}')

    def perform_destroy(self, instance):
        record_activity(
            self.request.user,
            'task_deleted',
            f'Deleted task {instance.title or instance.description[:80]}',
            assignment=instance.group.assignment,
            group=instance.group,
            task=instance,
        )
        log_audit(self.request.user, f'Deleted task {instance.pk}')
        instance.delete()


class TaskUpdateViewSet(viewsets.ModelViewSet):
    serializer_class = TaskUpdateSerializer
    permission_classes = [TaskManagementPermission]
    student_write_actions = ('create',)

    def get_queryset(self):
        user = self.request.user
        if user.is_admin_role():
            return TaskUpdate.objects.all()
        if user.is_lecturer():
            managed = Course.objects.filter(
                Q(lecturer=user) | Q(lecturer_links__lecturer=user)
            )
            return TaskUpdate.objects.filter(task__group__assignment__course__in=managed)
        return TaskUpdate.objects.filter(
            task__group__memberships__student=user,
            task__group__memberships__status=GroupMembership.STATUS_APPROVED,
            task__group__status__in=(Group.STATUS_ACTIVE, Group.STATUS_CLOSED),
        ).distinct()

    def perform_create(self, serializer):
        update = serializer.save(author=self.request.user)
        record_activity(
            self.request.user,
            'task_update',
            'Posted a task progress update',
            assignment=update.task.group.assignment,
            group=update.task.group,
            task=update.task,
        )
        log_audit(self.request.user, f'Added progress update to task {update.task_id}')

    def perform_update(self, serializer):
        update = serializer.save()
        record_activity(
            self.request.user,
            'task_update_edited',
            'Edited a task progress update',
            assignment=update.task.group.assignment,
            group=update.task.group,
            task=update.task,
        )
        log_audit(self.request.user, f'Edited progress update {update.pk}')

    def perform_destroy(self, instance):
        task = instance.task
        record_activity(
            self.request.user,
            'task_update_deleted',
            'Deleted a task progress update',
            assignment=task.group.assignment,
            group=task.group,
            task=task,
        )
        log_audit(self.request.user, f'Deleted progress update {instance.pk}')
        instance.delete()
