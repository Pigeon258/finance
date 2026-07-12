from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models


class Account(models.Model):
    class AccountType(models.TextChoices):
        BANK = "BANK", "银行卡"
        WECHAT = "WECHAT", "微信余额"
        ALIPAY = "ALIPAY", "支付宝余额"
        CREDIT_CARD = "CREDIT_CARD", "信用卡"

    class BalanceNature(models.TextChoices):
        ASSET = "ASSET", "资产"
        LIABILITY = "LIABILITY", "负债"

    name = models.CharField("名称", max_length=100)
    account_type = models.CharField("账户类型", max_length=20, choices=AccountType.choices)
    balance_nature = models.CharField("余额性质", max_length=12, choices=BalanceNature.choices)
    initial_balance = models.DecimalField(
        "初始余额",
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    is_active = models.BooleanField("启用", default=True)
    sort_order = models.PositiveIntegerField("显示顺序", default=0)
    opened_at = models.DateField("开户日期", null=True, blank=True)
    note = models.TextField("备注", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(initial_balance__gte=Decimal("0.00")),
                name="accounts_initial_balance_nonnegative",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        account_type="CREDIT_CARD",
                        balance_nature="LIABILITY",
                    )
                    | (
                        ~models.Q(account_type="CREDIT_CARD")
                        & models.Q(balance_nature="ASSET")
                    )
                ),
                name="accounts_type_matches_balance_nature",
            ),
            models.UniqueConstraint(
                fields=["account_type"],
                condition=models.Q(account_type="CREDIT_CARD", is_active=True),
                name="accounts_one_active_credit_card",
            ),
        ]
        indexes = [
            models.Index(
                fields=["is_active", "sort_order"], name="accounts_ac_is_acti_24960f_idx"
            )
        ]

    def __str__(self) -> str:
        return self.name
