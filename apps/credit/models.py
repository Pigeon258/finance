from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.accounts.models import Account


class CreditCardProfile(models.Model):
    account = models.OneToOneField(
        Account, on_delete=models.PROTECT, related_name="credit_card_profile", verbose_name="账户"
    )
    credit_limit = models.DecimalField(
        "信用额度",
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    personal_monthly_limit = models.DecimalField(
        "个人月度消费上限",
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    statement_day = models.PositiveSmallIntegerField(
        "账单日", validators=[MinValueValidator(1), MaxValueValidator(31)]
    )
    due_day = models.PositiveSmallIntegerField(
        "还款日", validators=[MinValueValidator(1), MaxValueValidator(31)]
    )
    is_active = models.BooleanField("启用", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["is_active"],
                condition=models.Q(is_active=True),
                name="credit_one_active_profile",
            ),
            models.CheckConstraint(
                condition=models.Q(credit_limit__gte=0), name="credit_limit_nonnegative"
            ),
            models.CheckConstraint(
                condition=models.Q(personal_monthly_limit__gte=0),
                name="credit_personal_limit_nonnegative",
            ),
        ]

    def __str__(self) -> str:
        return self.account.name

    def clean(self) -> None:
        super().clean()
        if self.account_id and self.account.balance_nature != Account.BalanceNature.LIABILITY:
            raise ValidationError({"account": "信用卡配置必须关联负债账户。"})


class BillingCycle(models.Model):
    class Status(models.TextChoices):
        OPEN = "OPEN", "未出账"
        ISSUED = "ISSUED", "待还款"
        PARTIALLY_PAID = "PARTIALLY_PAID", "部分还款"
        PAID = "PAID", "已全额还款"
        OVERDUE = "OVERDUE", "已逾期"

    credit_card_profile = models.ForeignKey(
        CreditCardProfile, on_delete=models.PROTECT, related_name="billing_cycles"
    )
    cycle_start = models.DateField("账期开始")
    cycle_end = models.DateField("账期结束")
    due_date = models.DateField("还款日")
    status = models.CharField("状态", max_length=20, choices=Status.choices, default=Status.OPEN)
    official_statement_amount = models.DecimalField(
        "正式账单金额", max_digits=14, decimal_places=2, null=True, blank=True
    )
    official_due_amount = models.DecimalField(
        "正式应还金额", max_digits=14, decimal_places=2, null=True, blank=True
    )
    note = models.TextField("备注", blank=True)
    issued_at = models.DateTimeField("确认出账时间", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-cycle_end", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["credit_card_profile", "cycle_start", "cycle_end"],
                name="credit_unique_billing_cycle",
            ),
            models.CheckConstraint(
                condition=models.Q(cycle_start__lte=models.F("cycle_end")),
                name="credit_cycle_start_lte_end",
            ),
            models.CheckConstraint(
                condition=models.Q(cycle_end__lt=models.F("due_date")),
                name="credit_cycle_end_before_due",
            ),
            models.CheckConstraint(
                condition=models.Q(official_statement_amount__isnull=True)
                | models.Q(official_statement_amount__gte=0),
                name="credit_official_statement_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(official_due_amount__isnull=True)
                | models.Q(official_due_amount__gte=0),
                name="credit_official_due_nonnegative",
            ),
        ]
        indexes = [models.Index(fields=["status", "due_date"])]

    def __str__(self) -> str:
        return f"{self.cycle_start} 至 {self.cycle_end}"


class BillingCycleItem(models.Model):
    class ItemType(models.TextChoices):
        CHARGE = "CHARGE", "普通消费"
        INSTALLMENT = "INSTALLMENT", "分期"
        REFUND = "REFUND", "退款"
        REPAYMENT = "REPAYMENT", "还款"
        FEE = "FEE", "手续费"
        ADJUSTMENT = "ADJUSTMENT", "调整"

    billing_cycle = models.ForeignKey(BillingCycle, on_delete=models.PROTECT, related_name="items")
    transaction = models.ForeignKey(
        "ledger.Transaction",
        on_delete=models.PROTECT,
        related_name="billing_cycle_items",
    )
    item_type = models.CharField("项目类型", max_length=12, choices=ItemType.choices)
    allocated_amount = models.DecimalField("分配金额", max_digits=14, decimal_places=2)
    note = models.CharField("备注", max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(allocated_amount__gt=0),
                name="credit_allocated_amount_positive",
            ),
            models.UniqueConstraint(
                fields=["billing_cycle", "transaction", "item_type"],
                name="credit_unique_cycle_transaction_type",
            ),
            models.UniqueConstraint(
                fields=["transaction"],
                condition=models.Q(item_type__in=["CHARGE", "INSTALLMENT"]),
                name="credit_charge_assigned_once",
            ),
            models.UniqueConstraint(
                fields=["transaction"],
                condition=models.Q(item_type="REFUND"),
                name="credit_refund_assigned_once",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_item_type_display()} {self.allocated_amount}"
