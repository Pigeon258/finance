from django.urls import path

from . import views

app_name = "credit"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("settings/", views.profile_settings, name="profile-settings"),
    path("purchases/new/", views.purchase_create, name="purchase-create"),
    path("repayments/new/", views.repayment_create, name="repayment-create"),
    path("cycles/<int:cycle_id>/", views.cycle_detail, name="cycle-detail"),
    path("cycles/<int:cycle_id>/issue/", views.cycle_issue, name="cycle-issue"),
    path(
        "cycles/<int:cycle_id>/refunds/<int:refund_id>/confirm/",
        views.confirm_refund,
        name="confirm-refund",
    ),
]
