from django.urls import path

from . import views

app_name = "wealth"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("accounts/new/", views.account_create, name="account-create"),
    path("accounts/<int:account_id>/edit/", views.account_edit, name="account-edit"),
    path("accounts/<int:account_id>/valuation/", views.valuation, name="valuation"),
    path("accounts/<int:account_id>/income/", views.income, name="income"),
    path("accounts/<int:account_id>/sync-yuebao/", views.sync_yuebao, name="sync-yuebao"),
    path("transfers/in/", views.transfer_in, name="transfer-in"),
    path("transfers/out/", views.transfer_out, name="transfer-out"),
]
