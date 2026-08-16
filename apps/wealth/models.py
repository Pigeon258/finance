from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models

from apps.accounts.models import Account


class WealthAccount(models.Model):
    class AccountType(models.TextChoices):
        DEPOSIT = "DEPOSIT", "存款"
        MONEY_FUND = "MONEY_FUND", "货币基金"
        BOND_FUND = "BOND_FUND", "债券基金"
        INDEX_FUND = "INDEX_FUND", "指数基金"
        OTHER = "OTHER", "其他"

    name = models.CharField("名称", max_length=100)
    account_type = models.CharField("理财类型", max_length=20, choices=AccountType.choices)
    institution = models.CharField("机构", max_length=100, blank=True)
    core_account = models.OneToOneField(
        Account,
        on_delete=models.PROTECT,
        related_name="wealth_account",
        verbose_name="关联核心账户",
    )
    current_value = models.DecimalField(
        "当前市值", max_digits=14, decimal_places=2, default=Decimal("0.00")
    )
    valuation_date = models.DateField("估值日期", null=True, blank=True)
    fund_code = models.CharField("基金代码", max_length=20, blank=True)
    auto_fetch_enabled = models.BooleanField("自动同步余额宝收益率", default=False)
    seven_day_annual_yield = models.DecimalField(
        "七日年化收益率（%）", max_digits=8, decimal_places=4, null=True, blank=True
    )
    per_ten_thousand_income = models.DecimalField(
        "每万份收益", max_digits=10, decimal_places=4, null=True, blank=True
    )
    last_sync_at = models.DateTimeField("最近同步时间", null=True, blank=True)
    is_active = models.BooleanField("启用", default=True)
    sort_order = models.PositiveIntegerField("显示顺序", default=0)
    opened_on = models.DateField("开户日期", null=True, blank=True)
    note = models.TextField("备注", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(current_value__gte=0),
                name="wealth_current_value_nonnegative",
            )
        ]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.core_account_id and self.core_account.account_type != Account.AccountType.WEALTH:
            raise ValidationError({"core_account": "理财账户必须关联核心账户类型“理财账户”。"})
        if self.auto_fetch_enabled and self.fund_code != "000198":
            raise ValidationError({"auto_fetch_enabled": "当前仅余额宝（000198）支持自动同步。"})


class WealthFlow(models.Model):
    class FlowType(models.TextChoices):
        TRANSFER_IN = "TRANSFER_IN", "转入理财"
        TRANSFER_OUT = "TRANSFER_OUT", "转出理财"
        INCOME = "INCOME", "理财收益"
        VALUATION = "VALUATION", "估值调整"

    wealth_account = models.ForeignKey(
        WealthAccount, on_delete=models.PROTECT, related_name="flows", verbose_name="理财账户"
    )
    flow_type = models.CharField("流水类型", max_length=20, choices=FlowType.choices)
    amount = models.DecimalField("金额", max_digits=14, decimal_places=2)
    occurred_on = models.DateField("发生日期")
    related_transaction = models.ForeignKey(
        "ledger.Transaction",
        on_delete=models.PROTECT,
        related_name="wealth_flows",
        null=True,
        blank=True,
        verbose_name="关联核心交易",
    )
    note = models.CharField("备注", max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_on", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(flow_type="VALUATION")
                    | models.Q(amount__gt=0)
                ),
                name="wealth_flow_amount_positive_for_cash_types",
            )
        ]

    def __str__(self):
        return f"{self.wealth_account} {self.get_flow_type_display()} {self.amount}"
