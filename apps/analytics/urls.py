from django.urls import path

from . import views

app_name = "analytics"

urlpatterns = [
    path("risk/", views.risk_overview, name="risk-overview"),
    path("installment-preview/", views.installment_preview, name="installment-preview"),
]
