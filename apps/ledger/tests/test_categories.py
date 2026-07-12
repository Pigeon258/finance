from decimal import Decimal
from importlib import import_module

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.ledger import selectors, services
from apps.ledger.models import Category


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_superuser(
        username="owner", password="correct horse battery staple"
    )


@pytest.mark.django_db
def test_default_categories_are_created_once():
    assert Category.objects.filter(category_type=Category.CategoryType.EXPENSE).count() == 13
    assert Category.objects.filter(category_type=Category.CategoryType.INCOME).count() == 1

    migration = import_module("apps.ledger.migrations.0002_default_categories")
    migration.create_default_categories(apps, None)

    assert Category.objects.count() == 14


@pytest.mark.django_db
def test_default_expense_categories_have_expected_necessity():
    necessary = set(
        Category.objects.filter(necessity=Category.Necessity.NECESSARY).values_list(
            "name", flat=True
        )
    )
    flexible = set(
        Category.objects.filter(necessity=Category.Necessity.FLEXIBLE).values_list(
            "name", flat=True
        )
    )

    assert necessary == {"餐饮", "交通", "日用品", "通信", "学习", "医疗", "固定订阅"}
    assert flexible == {"娱乐", "社交", "服饰", "数码产品", "旅行", "其他"}


@pytest.mark.django_db
def test_income_category_has_no_necessity():
    income = Category.objects.get(category_type=Category.CategoryType.INCOME)
    assert income.necessity is None


@pytest.mark.django_db
def test_create_expense_category_requires_necessity():
    with pytest.raises(ValidationError):
        services.create_category(
            name="测试分类",
            category_type=Category.CategoryType.EXPENSE,
            necessity=None,
            default_budget=Decimal("0.00"),
            is_active=True,
            sort_order=200,
        )


@pytest.mark.django_db
def test_income_category_discards_necessity_and_uses_decimal_budget():
    category = services.create_category(
        name="奖学金",
        category_type=Category.CategoryType.INCOME,
        necessity=Category.Necessity.FLEXIBLE,
        default_budget=Decimal("100.25"),
        is_active=True,
        sort_order=20,
    )

    assert category.necessity is None
    assert category.default_budget == Decimal("100.25")


@pytest.mark.django_db
def test_negative_default_budget_is_rejected():
    with pytest.raises(ValidationError):
        services.create_category(
            name="非法预算",
            category_type=Category.CategoryType.EXPENSE,
            necessity=Category.Necessity.FLEXIBLE,
            default_budget=Decimal("-0.01"),
            is_active=True,
            sort_order=200,
        )


@pytest.mark.django_db
def test_category_can_be_deactivated_without_deletion():
    category = Category.objects.get(name="娱乐")
    services.deactivate_category(category=category)

    assert Category.objects.filter(pk=category.pk).exists()
    assert category not in selectors.category_list(include_inactive=False)
    assert category in selectors.category_list(include_inactive=True)


@pytest.mark.django_db
def test_category_management_pages_require_login(client):
    assert client.get(reverse("ledger:category-index")).status_code == 302


@pytest.mark.django_db
def test_category_can_be_created_and_edited_through_pages(client, owner):
    client.force_login(owner)
    response = client.post(
        reverse("ledger:category-create"),
        {
            "name": "兼职工具",
            "category_type": Category.CategoryType.EXPENSE,
            "necessity": Category.Necessity.FLEXIBLE,
            "default_budget": "50.00",
            "is_active": "on",
            "sort_order": 200,
        },
    )
    category = Category.objects.get(name="兼职工具")

    assert response.status_code == 302
    assert category.default_budget == Decimal("50.00")

    response = client.post(
        reverse("ledger:category-edit", args=[category.id]),
        {
            "name": "兼职用品",
            "category_type": Category.CategoryType.INCOME,
            "necessity": Category.Necessity.NECESSARY,
            "default_budget": "60.00",
            "is_active": "on",
            "sort_order": 210,
        },
    )
    category.refresh_from_db()
    assert response.status_code == 302
    assert category.name == "兼职用品"
    assert category.category_type == Category.CategoryType.EXPENSE
