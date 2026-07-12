from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models


class Category(models.Model):
    class CategoryType(models.TextChoices):
        INCOME = "INCOME", "收入"
        EXPENSE = "EXPENSE", "支出"

    class Necessity(models.TextChoices):
        NECESSARY = "NECESSARY", "必要消费"
        FLEXIBLE = "FLEXIBLE", "弹性消费"

    name = models.CharField("名称", max_length=100)
    category_type = models.CharField("分类类型", max_length=10, choices=CategoryType.choices)
    necessity = models.CharField(  # noqa: DJ001 - income categories have no necessity.
        "消费性质", max_length=10, choices=Necessity.choices, null=True, blank=True
    )
    default_budget = models.DecimalField(
        "默认预算",
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    is_active = models.BooleanField("启用", default=True)
    sort_order = models.PositiveIntegerField("显示顺序", default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["category_type", "sort_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["category_type", "name"], name="ledger_unique_category_name_per_type"
            ),
            models.CheckConstraint(
                condition=models.Q(default_budget__gte=Decimal("0.00")),
                name="ledger_default_budget_nonnegative",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(category_type="EXPENSE", necessity__isnull=False)
                    | models.Q(category_type="INCOME", necessity__isnull=True)
                ),
                name="ledger_category_necessity_matches_type",
            ),
        ]
        indexes = [models.Index(fields=["category_type", "is_active", "sort_order"])]

    def __str__(self) -> str:
        return self.name
