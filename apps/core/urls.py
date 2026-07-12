from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("health/live", views.health_live, name="health-live"),
    path("health/ready", views.health_ready, name="health-ready"),
]
