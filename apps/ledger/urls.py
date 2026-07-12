from django.urls import path

from . import views

app_name = "ledger"

urlpatterns = [
    path("categories/", views.category_index, name="category-index"),
    path("categories/new/", views.category_create, name="category-create"),
    path("categories/<int:category_id>/edit/", views.category_edit, name="category-edit"),
    path(
        "categories/<int:category_id>/deactivate/",
        views.category_deactivate,
        name="category-deactivate",
    ),
]
