from django.urls import path

from . import views

app_name = "analytics"

urlpatterns = [
    path("dashboard/", views.dashboard, name="dashboard"),
    path("upcoming/", views.upcoming, name="upcoming"),
    path("reports/", views.reports, name="reports"),
    path("risk/", views.risk_overview, name="risk-overview"),
    path("installment-preview/", views.installment_preview, name="installment-preview"),
]
