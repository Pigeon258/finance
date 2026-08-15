from django.urls import path

from . import views

app_name = "budgets"

urlpatterns = [
    path("", views.index, name="index"),
    path("monthly/edit/", views.monthly_budget_edit, name="monthly-edit"),
    path("monthly/copy-previous/", views.copy_previous, name="copy-previous"),
    path(
        "monthly/<int:budget_id>/categories/",
        views.category_budgets_edit,
        name="category-budgets-edit",
    ),
    path(
        "monthly/<int:budget_id>/categories/edit/", views.category_budget_edit, name="category-edit"
    ),
    path("reserve/new/", views.reserve_create, name="reserve-create"),
    path("cash-flows/", views.cash_flow_index, name="cash-flow-index"),
    path("cash-flows/new/", views.cash_flow_create, name="cash-flow-create"),
    path("cash-flows/<int:plan_id>/generate/", views.generate, name="generate"),
    path("cash-flows/<int:plan_id>/toggle/", views.plan_toggle, name="plan-toggle"),
    path(
        "occurrences/<int:occurrence_id>/confirm/",
        views.occurrence_confirm,
        name="occurrence-confirm",
    ),
    path("occurrences/<int:occurrence_id>/skip/", views.occurrence_skip, name="occurrence-skip"),
]
