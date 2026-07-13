from django.urls import include, path

from apps.analytics import views as analytics_views

urlpatterns = [
    path("", analytics_views.dashboard, name="dashboard-root"),
    path("", include("apps.core.urls")),
    path("accounts/", include("apps.accounts.urls")),
    path("ledger/", include("apps.ledger.urls")),
    path("credit/", include("apps.credit.urls")),
    path("installments/", include("apps.installments.urls")),
    path("budgets/", include("apps.budgets.urls")),
    path("analytics/", include("apps.analytics.urls")),
    path("imports/", include("apps.imports.urls")),
]
