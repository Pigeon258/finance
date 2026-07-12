from django.urls import path

from . import views

app_name = "ledger"

urlpatterns = [
    path("transactions/", views.transaction_index, name="transaction-index"),
    path(
        "transactions/new/<str:operation>/", views.transaction_create, name="transaction-create"
    ),
    path(
        "transactions/<int:transaction_id>/",
        views.transaction_detail,
        name="transaction-detail",
    ),
    path(
        "transactions/<int:transaction_id>/edit/",
        views.transaction_edit,
        name="transaction-edit",
    ),
    path(
        "transactions/<int:transaction_id>/void/",
        views.transaction_void,
        name="transaction-void",
    ),
    path("categories/", views.category_index, name="category-index"),
    path("categories/new/", views.category_create, name="category-create"),
    path("categories/<int:category_id>/edit/", views.category_edit, name="category-edit"),
    path(
        "categories/<int:category_id>/deactivate/",
        views.category_deactivate,
        name="category-deactivate",
    ),
]
