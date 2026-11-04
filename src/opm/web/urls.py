"""No public signup, raw RPC relay, arbitrary shell, or admin debug endpoint."""
from django.urls import path, reverse_lazy
from django.contrib.auth.views import PasswordChangeView
from django.contrib.auth.decorators import login_required
from . import views

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('', views.dashboard, name='dashboard'),
    path('dashboard/connectivity/', views.dashboard_connectivity, name='dashboard_connectivity'),
    path('servers/', views.servers, name='servers'),
    path('servers/add/', views.server_add, name='server_add'),
    path('servers/enroll/fingerprint/', views.enrollment_fingerprint, name='enrollment_fingerprint'),
    path('servers/<str:host_id>/setup/', views.server_enroll, name='server_enroll'),
    path('servers/<str:host_id>/edit/', views.server_edit, name='server_edit'),
    path('servers/<str:host_id>/', views.server_detail, name='server_detail'),
    path('servers/<str:host_id>/action/<str:action>/', views.host_action, name='host_action'),
    path('servers/<str:host_id>/trust/', views.host_trust, name='host_trust'),
    path('servers/<str:host_id>/bootstrap/', views.host_bootstrap, name='host_bootstrap'),
    path('patch/', views.patch_manager, name='patch_manager'),
    path('patch/scan/', views.scan_all, name='scan_all'),
    path('patch/scan/authorization/', views.scan_authorization, name='scan_authorization'),
    path('patch/review/', views.create_plan, name='create_plan'),
    path('patch/plan/<str:plan_id>/', views.plan_review, name='plan_review'),
    path('patch/plan/<str:plan_id>/execute/', views.plan_execute, name='plan_execute'),
    path('activity/feed/', views.activity_feed, name='activity_feed'),
    path('activity/<str:activity_id>/', views.activity_detail, name='activity_detail'),
    path('history/', views.history, name='history'),
    path('history/clear/', views.clear_history, name='clear_history'),
    path('history/dismiss-alerts/', views.dismiss_alerts, name='dismiss_alerts'),
    path('history/<str:job_id>/', views.job_detail, name='job_detail'),
    path('history/<str:job_id>/cancel/', views.cancel_job, name='cancel_job'),
    path('settings/', views.settings_view, name='settings'),
    path('settings/password/', login_required(PasswordChangeView.as_view(
        template_name='web/change_password.html', success_url=reverse_lazy('settings'))), name='change_password'),
    path('settings/users/<int:user_id>/edit/', views.edit_account, name='edit_account'),
    path('settings/users/<int:user_id>/disable/', views.disable_account, name='disable_account'),
]
