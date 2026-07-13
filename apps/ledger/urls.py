from django.urls import path

from . import views

app_name = "ledger"

urlpatterns = [
    path("transactions/", views.transaction_index, name="transaction-index"),
    path("transactions/new/<str:operation>/", views.transaction_create, name="transaction-create"),
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
        "transactions/<int:transaction_id>/copy/",
        views.transaction_copy,
        name="transaction-copy",
    ),
    path(
        "transactions/<int:transaction_id>/void/",
        views.transaction_void,
        name="transaction-void",
    ),
    path(
        "transactions/<int:transaction_id>/refund/",
        views.transaction_refund,
        name="transaction-refund",
    ),
    path(
        "transactions/<int:transaction_id>/correct/",
        views.transaction_correct,
        name="transaction-correct",
    ),
    path(
        "reconciliations/accounts/<int:account_id>/",
        views.account_reconcile,
        name="account-reconcile",
    ),
    path("categories/", views.category_index, name="category-index"),
    path("categories/new/", views.category_create, name="category-create"),
    path("categories/<int:category_id>/edit/", views.category_edit, name="category-edit"),
    path(
        "categories/<int:category_id>/deactivate/",
        views.category_deactivate,
        name="category-deactivate",
    ),
    path("templates/", views.transaction_template_index, name="transaction-template-index"),
    path("templates/new/", views.transaction_template_edit, name="transaction-template-create"),
    path(
        "templates/<int:template_id>/edit/",
        views.transaction_template_edit,
        name="transaction-template-edit",
    ),
]
