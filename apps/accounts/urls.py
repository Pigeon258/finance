from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("", views.account_index, name="index"),
    path("new/", views.account_create, name="create"),
    path("<int:account_id>/edit/", views.account_edit, name="edit"),
    path("<int:account_id>/deactivate/", views.account_deactivate, name="deactivate"),
]
