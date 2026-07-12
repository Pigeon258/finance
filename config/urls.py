from django.urls import include, path

urlpatterns = [
    path("", include("apps.core.urls")),
    path("accounts/", include("apps.accounts.urls")),
    path("ledger/", include("apps.ledger.urls")),
]
