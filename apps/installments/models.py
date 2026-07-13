from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from apps.ledger.models import Category, Transaction


class InstallmentPlan(models.Model):
    class SourceType(models.TextChoices):
        CREDIT_CARD = "CREDIT_CARD", "信用卡"
        PLATFORM = "PLATFORM", "平台分期"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "进行中"
        COMPLETED = "COMPLETED", "已完成"
        EARLY_SETTLED = "EARLY_SETTLED", "提前结清"
        CANCELLED = "CANCELLED", "已取消"
        REFUND_PROCESSING = "REFUND_PROCESSING", "退款处理中"

    product_name = models.CharField("商品名称", max_length=200)
    purchase_date = models.DateField("购买日期")
    original_price = models.DecimalField(
        "商品原价",
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="installment_plans", verbose_name="分类"
    )
    source_type = models.CharField("分期来源", max_length=16, choices=SourceType.choices)
    installment_count = models.PositiveSmallIntegerField("分期期数")
    default_installment_amount = models.DecimalField(
        "默认每期金额",
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    first_due_month = models.DateField("首期月份")
    total_repayment_amount = models.DecimalField(
        "总还款金额",
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    status = models.CharField("状态", max_length=20, choices=Status.choices, default=Status.ACTIVE)
    note = models.TextField("备注", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-purchase_date", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(original_price__gt=0),
                name="installments_original_price_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(installment_count__gt=0), name="installments_count_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(default_installment_amount__gt=0),
                name="installments_default_amount_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(total_repayment_amount__gt=0),
                name="installments_total_repayment_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(first_due_month__day=1),
                name="installments_first_due_month_first_day",
            ),
        ]

    def __str__(self) -> str:
        return self.product_name

    def clean(self) -> None:
        super().clean()
        if self.category_id and self.category.category_type != Category.CategoryType.EXPENSE:
            raise ValidationError({"category": "分期计划必须使用支出分类。"})
        if self.first_due_month and self.first_due_month.day != 1:
            raise ValidationError({"first_due_month": "首期月份必须使用该月第一天。"})


class InstallmentItem(models.Model):
    class Status(models.TextChoices):
        PLANNED = "PLANNED", "待发生"
        POSTED = "POSTED", "已入账"
        CANCELLED = "CANCELLED", "已取消"
        WAIVED = "WAIVED", "已豁免"

    plan = models.ForeignKey(
        InstallmentPlan, on_delete=models.PROTECT, related_name="items", verbose_name="分期计划"
    )
    sequence_number = models.PositiveSmallIntegerField("期次")
    due_date = models.DateField("预计到期日")
    due_month = models.DateField("预算月份")
    planned_amount = models.DecimalField(
        "计划金额",
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    actual_amount = models.DecimalField(
        "实际金额", max_digits=14, decimal_places=2, null=True, blank=True
    )
    status = models.CharField("状态", max_length=12, choices=Status.choices, default=Status.PLANNED)
    ledger_transaction = models.OneToOneField(
        Transaction,
        on_delete=models.PROTECT,
        related_name="installment_item",
        null=True,
        blank=True,
        verbose_name="正式交易",
    )
    billing_cycle = models.ForeignKey(
        "credit.BillingCycle",
        on_delete=models.PROTECT,
        related_name="installment_items",
        null=True,
        blank=True,
        verbose_name="信用卡账期",
    )
    posted_at = models.DateTimeField("入账时间", null=True, blank=True)
    note = models.TextField("备注", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["plan", "sequence_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["plan", "sequence_number"], name="installments_unique_plan_sequence"
            ),
            models.CheckConstraint(
                condition=models.Q(planned_amount__gt=0),
                name="installments_planned_amount_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(actual_amount__isnull=True) | models.Q(actual_amount__gt=0),
                name="installments_actual_amount_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(due_month__day=1),
                name="installments_due_month_first_day",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="POSTED",
                        actual_amount__isnull=False,
                        ledger_transaction__isnull=False,
                        posted_at__isnull=False,
                    )
                    | (
                        ~models.Q(status="POSTED")
                        & models.Q(actual_amount__isnull=True)
                        & models.Q(ledger_transaction__isnull=True)
                        & models.Q(billing_cycle__isnull=True)
                        & models.Q(posted_at__isnull=True)
                    )
                ),
                name="installments_posted_relationships_consistent",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "due_month"]),
            models.Index(fields=["status", "due_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.plan} 第 {self.sequence_number} 期"

    def clean(self) -> None:
        super().clean()
        if self.due_date and self.due_month != self.due_date.replace(day=1):
            raise ValidationError({"due_month": "预算月份必须等于预计到期日所在月第一天。"})
        if (
            self.billing_cycle_id
            and self.plan.source_type != InstallmentPlan.SourceType.CREDIT_CARD
        ):
            raise ValidationError({"billing_cycle": "只有信用卡分期可以关联信用卡账期。"})
        is_posted = self.status == self.Status.POSTED
        posted_fields_complete = bool(
            self.actual_amount is not None
            and self.ledger_transaction_id
            and self.posted_at is not None
        )
        if is_posted != posted_fields_complete:
            raise ValidationError("已入账状态与正式交易、实际金额和入账时间必须保持一致。")


class InstallmentAdjustment(models.Model):
    class AdjustmentType(models.TextChoices):
        AMOUNT_CHANGE = "AMOUNT_CHANGE", "金额变更"
        CANCEL_REMAINING = "CANCEL_REMAINING", "取消剩余期次"
        REFUND = "REFUND", "退款"
        EARLY_SETTLEMENT = "EARLY_SETTLEMENT", "提前结清"
        MANUAL_CORRECTION = "MANUAL_CORRECTION", "手工修正"

    plan = models.ForeignKey(
        InstallmentPlan,
        on_delete=models.PROTECT,
        related_name="adjustments",
        verbose_name="分期计划",
    )
    installment_item = models.ForeignKey(
        InstallmentItem,
        on_delete=models.PROTECT,
        related_name="adjustments",
        null=True,
        blank=True,
        verbose_name="分期期次",
    )
    adjustment_type = models.CharField("调整类型", max_length=20, choices=AdjustmentType.choices)
    amount_delta = models.DecimalField("金额变化", max_digits=14, decimal_places=2)
    effective_date = models.DateField("生效日期")
    related_transaction = models.ForeignKey(
        Transaction,
        on_delete=models.PROTECT,
        related_name="installment_adjustments",
        null=True,
        blank=True,
        verbose_name="关联交易",
    )
    note = models.TextField("备注", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-effective_date", "-id"]

    def __str__(self) -> str:
        return f"{self.plan}：{self.get_adjustment_type_display()}"

    def clean(self) -> None:
        super().clean()
        if self.installment_item_id and self.installment_item.plan_id != self.plan_id:
            raise ValidationError({"installment_item": "调整期次必须属于所选分期计划。"})
        if self.adjustment_type == self.AdjustmentType.AMOUNT_CHANGE and self.amount_delta == 0:
            raise ValidationError({"amount_delta": "金额变更不得为零。"})
        if self.adjustment_type == self.AdjustmentType.REFUND:
            if self.installment_item_id is None:
                raise ValidationError({"installment_item": "退款调整必须关联具体期次。"})
            if self.amount_delta >= 0:
                raise ValidationError({"amount_delta": "退款调整必须减少金额。"})
            if self.related_transaction_id and (
                self.related_transaction.transaction_type != Transaction.TransactionType.REFUND
            ):
                raise ValidationError({"related_transaction": "已入账退款必须关联退款交易。"})
