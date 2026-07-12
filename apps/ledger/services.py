from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Category


def normalized_necessity(*, category_type: str, necessity: str | None) -> str | None:
    if category_type == Category.CategoryType.INCOME:
        return None
    return necessity


@transaction.atomic
def create_category(
    *,
    name: str,
    category_type: str,
    necessity: str | None,
    default_budget: Decimal,
    is_active: bool,
    sort_order: int,
) -> Category:
    category = Category(
        name=name,
        category_type=category_type,
        necessity=normalized_necessity(category_type=category_type, necessity=necessity),
        default_budget=default_budget,
        is_active=is_active,
        sort_order=sort_order,
    )
    category.full_clean()
    category.save()
    return category


@transaction.atomic
def update_category(
    *,
    category: Category,
    name: str,
    necessity: str | None,
    default_budget: Decimal,
    is_active: bool,
    sort_order: int,
) -> Category:
    category.name = name
    category.necessity = normalized_necessity(
        category_type=category.category_type, necessity=necessity
    )
    category.default_budget = default_budget
    category.is_active = is_active
    category.sort_order = sort_order
    category.full_clean()
    category.save(
        update_fields=[
            "name",
            "necessity",
            "default_budget",
            "is_active",
            "sort_order",
            "updated_at",
        ]
    )
    return category


@transaction.atomic
def deactivate_category(*, category: Category) -> Category:
    if not category.is_active:
        raise ValidationError("分类已经停用。")
    category.is_active = False
    category.full_clean()
    category.save(update_fields=["is_active", "updated_at"])
    return category
