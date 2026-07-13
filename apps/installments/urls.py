from django.urls import path

from . import views

app_name = "installments"

urlpatterns = [
    path("", views.index, name="index"),
    path("new/", views.plan_create, name="create"),
    path("<int:plan_id>/", views.detail, name="detail"),
    path("<int:plan_id>/cancel/", views.cancel, name="cancel"),
    path("<int:plan_id>/early-settlement/", views.early_settlement, name="early-settlement"),
    path("<int:plan_id>/refund/start/", views.refund_start, name="refund-start"),
    path("<int:plan_id>/refund/finish/", views.refund_finish, name="refund-finish"),
    path("items/<int:item_id>/post/", views.item_post, name="item-post"),
    path("items/<int:item_id>/adjust/", views.item_adjust, name="item-adjust"),
    path("items/<int:item_id>/refund/", views.item_refund, name="item-refund"),
]
