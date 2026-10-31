from django.urls import include, path
from django.contrib.auth import views as auth_views
from django.urls import reverse_lazy
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r'users', views.UserViewSet, basename='api-users')
router.register(r'courses', views.CourseViewSet, basename='api-courses')
router.register(r'assignments', views.AssignmentViewSet, basename='api-assignments')
router.register(r'groups', views.GroupViewSet, basename='api-groups')
router.register(r'tasks', views.TaskViewSet, basename='api-tasks')
router.register(r'taskupdates', views.TaskUpdateViewSet, basename='api-taskupdates')

urlpatterns = [
    path('login/', views.RoleLoginView.as_view(), name='login'),
    path('logout/', views.RoleLogoutView.as_view(), name='logout'),
    path(
        'password-reset/',
        auth_views.PasswordResetView.as_view(
            template_name='core/password_reset_form.html',
            email_template_name='core/password_reset_email.html',
            subject_template_name='core/password_reset_subject.txt',
            success_url=reverse_lazy('password_reset_done'),
        ),
        name='password_reset',
    ),
    path(
        'password-reset/done/',
        auth_views.PasswordResetDoneView.as_view(
            template_name='core/password_reset_done.html',
        ),
        name='password_reset_done',
    ),
    path(
        'password-reset/<uidb64>/<token>/',
        auth_views.PasswordResetConfirmView.as_view(
            template_name='core/password_reset_confirm.html',
            success_url=reverse_lazy('password_reset_complete'),
        ),
        name='password_reset_confirm',
    ),
    path(
        'password-reset/complete/',
        auth_views.PasswordResetCompleteView.as_view(
            template_name='core/password_reset_complete.html',
        ),
        name='password_reset_complete',
    ),
    path('', views.dashboard, name='dashboard'),
    path('groups/', views.group_list, name='group_list'),
    path('tasks/', views.task_list, name='task_list'),
    path('reports/', views.reports, name='reports'),
    path('contributions/', views.contribution_report, name='contribution_report'),
    path('profile/', views.profile, name='profile'),
    path('courses/', views.course_list, name='course_list'),
    path('courses/new/', views.course_create, name='course_create'),
    path('courses/<int:pk>/', views.course_detail, name='course_detail'),
    path('courses/<int:pk>/edit/', views.course_edit, name='course_edit'),
    path('academic-calendar/', views.academic_calendar, name='academic_calendar'),
    path(
        'academic-calendar/sessions/<int:pk>/edit/',
        views.academic_session_edit,
        name='academic_session_edit',
    ),
    path(
        'academic-calendar/semesters/<int:pk>/edit/',
        views.semester_edit,
        name='semester_edit',
    ),
    path('assignments/', views.assignment_list, name='assignment_list'),
    path('assignments/new/', views.assignment_create, name='assignment_create'),
    path('assignments/<int:pk>/', views.assignment_detail, name='assignment_detail'),
    path('assignments/<int:pk>/manage/', views.assignment_edit, name='assignment_edit'),
    path(
        'assignments/<int:assignment_id>/groups/new/',
        views.assignment_group_create,
        name='assignment_group_create',
    ),
    path('groups/new/', views.group_create, name='group_create'),
    path('groups/<int:pk>/', views.group_detail, name='group_detail'),
    path(
        'groups/documents/<int:pk>/download/',
        views.group_document_download,
        name='group_document_download',
    ),
    path(
        'groups/documents/<int:pk>/delete/',
        views.group_document_delete,
        name='group_document_delete',
    ),
    path('groups/<int:group_id>/tasks/new/', views.task_create, name='task_create'),
    path('tasks/<int:pk>/', views.task_detail, name='task_detail'),
    path('evidence/<int:pk>/download/', views.evidence_download, name='evidence_download'),
    path('assignments/<int:assignment_id>/peer-review/', views.peer_review_create, name='peer_review_create'),
    path(
        'assignments/<int:assignment_id>/peer-evaluations/',
        views.peer_evaluation_report,
        name='peer_evaluation_report',
    ),
    path('notifications/', views.notifications, name='notifications'),
    path('submissions/', views.submission_list, name='submission_list'),
    path('submissions/<int:pk>/receipt/', views.submission_receipt, name='submission_receipt'),
    path('submissions/<int:pk>/download/', views.submission_download, name='submission_download'),
    path('api/', include(router.urls)),
]
