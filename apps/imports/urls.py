from django.urls import path

from . import views

app_name = "imports"

urlpatterns = [
    path("", views.index, name="index"),
    path("upload/", views.upload, name="upload"),
    path("<int:batch_id>/", views.detail, name="detail"),
    path("<int:batch_id>/action/", views.batch_action, name="batch-action"),
    path("<int:batch_id>/reapply/", views.reapply_review, name="reapply-review"),
    path(
        "<int:batch_id>/records/<int:record_id>/",
        views.record_review,
        name="record-review",
    ),
    path("rules/", views.rules, name="rules"),
    path("rules/categories/new/", views.category_rule_edit, name="category-rule-create"),
    path(
        "rules/categories/<int:rule_id>/",
        views.category_rule_edit,
        name="category-rule-edit",
    ),
    path("rules/accounts/new/", views.account_rule_edit, name="account-rule-create"),
    path(
        "rules/accounts/<int:rule_id>/",
        views.account_rule_edit,
        name="account-rule-edit",
    ),
]
