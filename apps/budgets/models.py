from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.accounts.models import Account
from apps.ledger.models import Category, Transaction


class MonthlyBudget(models.Model):
    month = models.DateField("预算月份", unique=True)
    total_expense_budget = models.DecimalField(
        "月度总支出预算",
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    savings_target = models.DecimalField(
        "储蓄目标",
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    minimum_safety_buffer = models.DecimalField(
        "最低安全余量",
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    note = models.TextField("备注", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-month"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(month__day=1), name="budgets_month_first_day"
            ),
            models.CheckConstraint(
                condition=models.Q(total_expense_budget__gte=0),
                name="budgets_total_expense_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(savings_target__gte=0),
                name="budgets_savings_target_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(minimum_safety_buffer__gte=0),
                name="budgets_safety_buffer_nonnegative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.month:%Y-%m} 预算"

    def clean(self) -> None:
        super().clean()
        if self.month and self.month.day != 1:
            raise ValidationError({"month": "预算月份必须使用该月第一天。"})


class CategoryBudget(models.Model):
    monthly_budget = models.ForeignKey(
        MonthlyBudget, on_delete=models.PROTECT, related_name="category_budgets"
    )
    name = models.CharField("预算项目名称", max_length=100)
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="category_budgets"
    )
    budget_amount = models.DecimalField(
        "项目预算金额",
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    warning_threshold = models.DecimalField(
        "提醒阈值（%）",
        max_digits=5,
        decimal_places=2,
        default=Decimal("80.00"),
        validators=[MinValueValidator(Decimal("0.00")), MaxValueValidator(Decimal("100.00"))],
    )
    sort_order = models.PositiveIntegerField("显示顺序", default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["monthly_budget", "name"], name="budgets_unique_month_budget_item"
            ),
            models.CheckConstraint(
                condition=models.Q(budget_amount__gte=0),
                name="budgets_category_amount_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(warning_threshold__gte=0) & models.Q(warning_threshold__lte=100),
                name="budgets_warning_threshold_range",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.monthly_budget.month:%Y-%m} {self.name}"

    def clean(self) -> None:
        super().clean()
        if self.category_id and self.category.category_type != Category.CategoryType.EXPENSE:
            raise ValidationError({"category": "分类预算只能使用支出分类。"})


class ReserveMovement(models.Model):
    class MovementType(models.TextChoices):
        CONTRIBUTION = "CONTRIBUTION", "转入储备"
        WITHDRAWAL = "WITHDRAWAL", "动用储备"
        CORRECTION = "CORRECTION", "储备修正"

    movement_type = models.CharField("变动类型", max_length=16, choices=MovementType.choices)
    amount = models.DecimalField("金额", max_digits=14, decimal_places=2)
    occurred_on = models.DateField("发生日期")
    related_transaction = models.ForeignKey(
        Transaction,
        on_delete=models.PROTECT,
        related_name="reserve_movements",
        null=True,
        blank=True,
        verbose_name="关联交易",
    )
    note = models.TextField("备注", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_on", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(movement_type="CORRECTION") & ~models.Q(amount=0))
                | (
                    models.Q(movement_type__in=["CONTRIBUTION", "WITHDRAWAL"])
                    & models.Q(amount__gt=0)
                ),
                name="budgets_reserve_amount_matches_type",
            )
        ]

    def __str__(self) -> str:
        return f"{self.get_movement_type_display()} {self.amount}"


class PlannedCashFlow(models.Model):
    class Direction(models.TextChoices):
        INCOME = "INCOME", "预计收入"
        EXPENSE = "EXPENSE", "固定支出"

    class Reliability(models.TextChoices):
        CERTAIN = "CERTAIN", "确定"
        LIKELY = "LIKELY", "较可能"
        UNCERTAIN = "UNCERTAIN", "不确定"

    class RecurrenceType(models.TextChoices):
        ONE_TIME = "ONE_TIME", "一次"
        MONTHLY = "MONTHLY", "每月"
        YEARLY = "YEARLY", "每年"

    name = models.CharField("名称", max_length=200)
    direction = models.CharField("方向", max_length=10, choices=Direction.choices)
    amount = models.DecimalField(
        "计划金额",
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="planned_cash_flows", verbose_name="分类"
    )
    default_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="planned_cash_flows",
        null=True,
        blank=True,
        verbose_name="默认账户",
    )
    reliability = models.CharField(
        "可靠程度", max_length=10, choices=Reliability.choices, default=Reliability.CERTAIN
    )
    recurrence_type = models.CharField("发生周期", max_length=10, choices=RecurrenceType.choices)
    start_date = models.DateField("开始日期")
    end_date = models.DateField("结束日期", null=True, blank=True)
    day_of_month = models.PositiveSmallIntegerField(
        "每月日期",
        validators=[MinValueValidator(1), MaxValueValidator(31)],
    )
    is_active = models.BooleanField("启用", default=True)
    note = models.TextField("备注", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["direction", "name", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="budgets_plan_amount_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(day_of_month__gte=1) & models.Q(day_of_month__lte=31),
                name="budgets_plan_day_range",
            ),
            models.CheckConstraint(
                condition=models.Q(end_date__isnull=True)
                | models.Q(end_date__gte=models.F("start_date")),
                name="budgets_plan_end_not_before_start",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        if self.category_id:
            expected_type = (
                Category.CategoryType.INCOME
                if self.direction == self.Direction.INCOME
                else Category.CategoryType.EXPENSE
            )
            if self.category.category_type != expected_type:
                raise ValidationError({"category": "计划方向与分类类型不匹配。"})
        if (
            self.direction == self.Direction.EXPENSE
            and self.reliability != self.Reliability.CERTAIN
        ):
            raise ValidationError({"reliability": "固定支出的可靠程度必须为确定。"})
        if self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "结束日期不得早于开始日期。"})


class PlannedCashFlowOccurrence(models.Model):
    class Status(models.TextChoices):
        PLANNED = "PLANNED", "待发生"
        CONFIRMED = "CONFIRMED", "已确认"
        SKIPPED = "SKIPPED", "已跳过"
        EXPIRED = "EXPIRED", "已过期"

    plan = models.ForeignKey(
        PlannedCashFlow, on_delete=models.PROTECT, related_name="occurrences", verbose_name="计划"
    )
    due_date = models.DateField("预计日期")
    planned_amount = models.DecimalField(
        "计划金额",
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    status = models.CharField("状态", max_length=10, choices=Status.choices, default=Status.PLANNED)
    linked_transaction = models.OneToOneField(
        Transaction,
        on_delete=models.PROTECT,
        related_name="planned_cash_flow_occurrence",
        null=True,
        blank=True,
        verbose_name="正式交易",
    )
    confirmed_at = models.DateTimeField("确认时间", null=True, blank=True)
    note = models.TextField("备注", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["due_date", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["plan", "due_date"], name="budgets_unique_plan_due_date"
            ),
            models.CheckConstraint(
                condition=models.Q(planned_amount__gt=0), name="budgets_occurrence_amount_positive"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="CONFIRMED",
                        linked_transaction__isnull=False,
                        confirmed_at__isnull=False,
                    )
                    | (
                        ~models.Q(status="CONFIRMED")
                        & models.Q(linked_transaction__isnull=True)
                        & models.Q(confirmed_at__isnull=True)
                    )
                ),
                name="budgets_occurrence_confirmation_consistent",
            ),
        ]
        indexes = [models.Index(fields=["status", "due_date"])]

    def __str__(self) -> str:
        return f"{self.plan} {self.due_date}"
