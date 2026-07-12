from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("settings/", views.settings_view, name="settings"),
    path("settings/password/", views.password_change_view, name="password-change"),
    path("health/live", views.health_live, name="health-live"),
    path("health/ready", views.health_ready, name="health-ready"),
]
