from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("settings/", views.settings_view, name="settings"),
    path("exports/", views.export_center, name="export-center"),
    path("exports/transactions.csv", views.export_transactions_csv, name="transactions-csv"),
    path(
        "exports/monthly-statistics.csv",
        views.export_monthly_statistics_csv,
        name="monthly-statistics-csv",
    ),
    path("exports/backup/", views.backup_download, name="backup-download"),
    path("exports/restore/", views.backup_restore, name="backup-restore"),
    path("settings/password/", views.password_change_view, name="password-change"),
    path(
        "settings/sessions/revoke-others/",
        views.sessions_revoke_others,
        name="sessions-revoke-others",
    ),
    path(
        "settings/sessions/<str:reference>/revoke/",
        views.session_revoke,
        name="session-revoke",
    ),
    path("health/live", views.health_live, name="health-live"),
    path("health/ready", views.health_ready, name="health-ready"),
]
