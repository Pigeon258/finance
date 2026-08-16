from decimal import Decimal

from django.core.exceptions import ValidationError
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


class Merchant(models.Model):
    name = models.CharField("商家名称", max_length=200)
    normalized_name = models.CharField("标准化名称", max_length=200, unique=True)
    is_active = models.BooleanField("启用", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self) -> str:
        return self.name


class Tag(models.Model):
    class AppliesTo(models.TextChoices):
        INCOME = "INCOME", "收入"
        EXPENSE = "EXPENSE", "支出"

    name = models.CharField("标签名称", max_length=50)
    applies_to = models.CharField(
        "适用类型", max_length=10, choices=AppliesTo.choices, default=AppliesTo.EXPENSE
    )
    is_active = models.BooleanField("启用", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["applies_to", "name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["applies_to", "name"],
                name="ledger_unique_tag_name_per_type",
            )
        ]

    def __str__(self) -> str:
        return f"{self.get_applies_to_display()}标签：{self.name}"


class Transaction(models.Model):
    class TransactionType(models.TextChoices):
        INCOME = "INCOME", "收入"
        EXPENSE = "EXPENSE", "支出"
        TRANSFER = "TRANSFER", "转账"
        REFUND = "REFUND", "退款"
        BALANCE_ADJUSTMENT = "BALANCE_ADJUSTMENT", "余额调整"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "有效"
        VOID = "VOID", "作废"
        REVERSED = "REVERSED", "已反向修正"

    class Source(models.TextChoices):
        MANUAL = "MANUAL", "手工"
        IMPORT = "IMPORT", "导入"
        SYSTEM = "SYSTEM", "系统"

    class Channel(models.TextChoices):
        BANK = "BANK", "银行"
        WECHAT = "WECHAT", "微信"
        ALIPAY = "ALIPAY", "支付宝"
        DIRECT = "DIRECT", "直接交易"
        OTHER = "OTHER", "其他"

    transaction_type = models.CharField("交易类型", max_length=24, choices=TransactionType.choices)
    status = models.CharField("状态", max_length=10, choices=Status.choices, default=Status.ACTIVE)
    amount = models.DecimalField("金额", max_digits=14, decimal_places=2)
    occurred_at = models.DateTimeField("发生时间")
    budget_month = models.DateField("预算月份", null=True, blank=True)
    category = models.ForeignKey(
        Category,
        verbose_name="分类",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="transactions",
    )
    channel = models.CharField("支付渠道", max_length=10, choices=Channel.choices)
    merchant = models.ForeignKey(
        Merchant,
        verbose_name="商家",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="transactions",
    )
    counterparty = models.CharField("商家或交易对象", max_length=200, blank=True)
    item_name = models.CharField("项目名称", max_length=200, blank=True)
    note = models.TextField("备注", blank=True)
    source = models.CharField("来源", max_length=10, choices=Source.choices, default=Source.MANUAL)
    related_transaction = models.ForeignKey(
        "self",
        verbose_name="关联交易",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="related_transactions",
    )
    is_financial_locked = models.BooleanField("财务锁定", default=False)
    voided_at = models.DateTimeField("作废时间", null=True, blank=True)
    void_reason = models.CharField("作废或修正原因", max_length=500, blank=True)
    tags = models.ManyToManyField(Tag, through="TransactionTag", related_name="transactions")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="ledger_amount_positive"),
            models.CheckConstraint(
                condition=(models.Q(budget_month__isnull=True) | models.Q(budget_month__day=1)),
                name="ledger_budget_month_first_day",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "occurred_at"]),
            models.Index(fields=["transaction_type", "occurred_at"]),
            models.Index(fields=["category", "budget_month"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_transaction_type_display()} {self.amount}"

    def clean(self) -> None:
        super().clean()
        categorized_types = {
            self.TransactionType.INCOME: Category.CategoryType.INCOME,
            self.TransactionType.EXPENSE: Category.CategoryType.EXPENSE,
            self.TransactionType.REFUND: Category.CategoryType.EXPENSE,
        }
        expected_category_type = categorized_types.get(self.transaction_type)
        if expected_category_type is None:
            if self.category_id is not None:
                raise ValidationError({"category": "该交易类型不得设置分类。"})
            if self.budget_month is not None:
                raise ValidationError({"budget_month": "该交易类型不得占用预算月份。"})
        else:
            if self.category_id is None or self.category.category_type != expected_category_type:
                raise ValidationError({"category": "分类类型与交易类型不匹配。"})
            if self.budget_month is None:
                raise ValidationError({"budget_month": "收入、支出和退款必须设置预算月份。"})
        if self.budget_month is not None and self.budget_month.day != 1:
            raise ValidationError({"budget_month": "预算月份必须使用该月第一天。"})
        if self.transaction_type == self.TransactionType.REFUND:
            if (
                self.related_transaction_id is None
                or self.related_transaction.transaction_type != self.TransactionType.EXPENSE
            ):
                raise ValidationError({"related_transaction": "退款必须关联原支出。"})


class TransactionEntry(models.Model):
    transaction = models.ForeignKey(
        Transaction, on_delete=models.CASCADE, related_name="entries", verbose_name="交易"
    )
    account = models.ForeignKey(
        "accounts.Account",
        on_delete=models.PROTECT,
        related_name="transaction_entries",
        verbose_name="账户",
    )
    balance_delta = models.DecimalField("余额变化", max_digits=14, decimal_places=2)
    note = models.CharField("备注", max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(balance_delta=0), name="ledger_entry_delta_nonzero"
            ),
            models.UniqueConstraint(
                fields=["transaction", "account"], name="ledger_one_entry_per_account"
            ),
        ]
        indexes = [models.Index(fields=["account", "transaction"])]

    def __str__(self) -> str:
        return f"{self.account}: {self.balance_delta}"


class TransactionTag(models.Model):
    transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE)
    tag = models.ForeignKey(Tag, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["transaction", "tag"], name="ledger_unique_transaction_tag"
            )
        ]

    def __str__(self) -> str:
        return f"{self.transaction_id}:{self.tag_id}"


class TransactionTemplate(models.Model):
    class Operation(models.TextChoices):
        INCOME = "income", "收入"
        EXPENSE = "expense", "普通支出"
        CREDIT_CARD_EXPENSE = "credit-card-expense", "信用卡消费"
        TRANSFER = "transfer", "账户转账"
        CREDIT_CARD_REPAYMENT = "credit-card-repayment", "信用卡还款"

    name = models.CharField("模板名称", max_length=100)
    operation = models.CharField("操作类型", max_length=30, choices=Operation.choices)
    amount = models.DecimalField("金额", max_digits=14, decimal_places=2)
    primary_account = models.ForeignKey(
        "accounts.Account",
        verbose_name="主要账户",
        on_delete=models.PROTECT,
        related_name="primary_transaction_templates",
    )
    secondary_account = models.ForeignKey(
        "accounts.Account",
        verbose_name="目标账户或信用卡",
        on_delete=models.PROTECT,
        related_name="secondary_transaction_templates",
        null=True,
        blank=True,
    )
    category = models.ForeignKey(
        Category,
        verbose_name="分类",
        on_delete=models.PROTECT,
        related_name="transaction_templates",
        null=True,
        blank=True,
    )
    channel = models.CharField("支付渠道", max_length=10, choices=Transaction.Channel.choices)
    counterparty = models.CharField("商家或交易对象", max_length=200, blank=True)
    item_name = models.CharField("项目名称", max_length=200, blank=True)
    note = models.TextField("备注", blank=True)
    is_active = models.BooleanField("启用", default=True)
    sort_order = models.PositiveIntegerField("显示顺序", default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="ledger_tpl_amount_positive"
            )
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        expense_operations = {
            self.Operation.EXPENSE,
            self.Operation.CREDIT_CARD_EXPENSE,
        }
        if self.operation == self.Operation.INCOME:
            if not self.category_id or self.category.category_type != Category.CategoryType.INCOME:
                raise ValidationError({"category": "收入模板必须选择收入分类。"})
        elif self.operation in expense_operations:
            if not self.category_id or self.category.category_type != Category.CategoryType.EXPENSE:
                raise ValidationError({"category": "支出模板必须选择支出分类。"})
        elif self.category_id is not None:
            raise ValidationError({"category": "转账和还款模板不能设置分类。"})

        if self.operation in {self.Operation.TRANSFER, self.Operation.CREDIT_CARD_REPAYMENT}:
            if self.secondary_account_id is None:
                raise ValidationError({"secondary_account": "该模板必须选择目标账户。"})
            if self.primary_account_id == self.secondary_account_id:
                raise ValidationError({"secondary_account": "两个账户不能相同。"})
        elif self.secondary_account_id is not None:
            raise ValidationError({"secondary_account": "该模板不需要第二个账户。"})

        if self.primary_account_id:
            expected = (
                "LIABILITY" if self.operation == self.Operation.CREDIT_CARD_EXPENSE else "ASSET"
            )
            if self.primary_account.balance_nature != expected:
                raise ValidationError({"primary_account": "主要账户性质与模板类型不匹配。"})
        if self.secondary_account_id:
            expected = (
                "LIABILITY" if self.operation == self.Operation.CREDIT_CARD_REPAYMENT else "ASSET"
            )
            if self.secondary_account.balance_nature != expected:
                raise ValidationError({"secondary_account": "目标账户性质与模板类型不匹配。"})
