from django import forms

from apps.accounts.models import Account

from .models import Category, Tag, Transaction


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = [
            "name",
            "category_type",
            "necessity",
            "default_budget",
            "is_active",
            "sort_order",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["category_type"].disabled = True
            self.fields["category_type"].help_text = "分类创建后不能修改收入/支出类型。"

    def clean(self):
        cleaned_data = super().clean()
        category_type = cleaned_data.get("category_type")
        necessity = cleaned_data.get("necessity")
        if category_type == Category.CategoryType.EXPENSE and not necessity:
            self.add_error("necessity", "支出分类必须选择消费性质。")
        if category_type == Category.CategoryType.INCOME:
            cleaned_data["necessity"] = None
        return cleaned_data


class BaseManualTransactionForm(forms.Form):
    amount = forms.DecimalField(label="金额", max_digits=14, decimal_places=2, min_value=0.01)
    occurred_at = forms.DateTimeField(
        label="日期和时间",
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}),
    )
    channel = forms.ChoiceField(label="支付渠道", choices=Transaction.Channel.choices)
    counterparty = forms.CharField(label="商家或交易对象", max_length=200, required=False)
    note = forms.CharField(label="备注", required=False, widget=forms.Textarea(attrs={"rows": 3}))
    tags = forms.ModelMultipleChoiceField(label="标签", queryset=Tag.objects.none(), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tags"].queryset = Tag.objects.filter(is_active=True).order_by("name")


class IncomeForm(BaseManualTransactionForm):
    account = forms.ModelChoiceField(label="实际账户", queryset=Account.objects.none())
    category = forms.ModelChoiceField(label="收入分类", queryset=Category.objects.none())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = Account.objects.filter(
            balance_nature=Account.BalanceNature.ASSET, is_active=True
        )
        self.fields["category"].queryset = Category.objects.filter(
            category_type=Category.CategoryType.INCOME, is_active=True
        )


class ExpenseForm(BaseManualTransactionForm):
    account = forms.ModelChoiceField(label="实际账户", queryset=Account.objects.none())
    category = forms.ModelChoiceField(label="支出分类", queryset=Category.objects.none())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = Account.objects.filter(
            balance_nature=Account.BalanceNature.ASSET, is_active=True
        )
        self.fields["category"].queryset = Category.objects.filter(
            category_type=Category.CategoryType.EXPENSE, is_active=True
        )


class CreditCardExpenseForm(ExpenseForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].label = "信用卡账户"
        self.fields["account"].queryset = Account.objects.filter(
            balance_nature=Account.BalanceNature.LIABILITY, is_active=True
        )


class TransferForm(forms.Form):
    amount = forms.DecimalField(label="金额", max_digits=14, decimal_places=2, min_value=0.01)
    occurred_at = BaseManualTransactionForm.base_fields["occurred_at"]
    source_account = forms.ModelChoiceField(label="转出账户", queryset=Account.objects.none())
    destination_account = forms.ModelChoiceField(label="转入账户", queryset=Account.objects.none())
    channel = forms.ChoiceField(label="支付渠道", choices=Transaction.Channel.choices)
    counterparty = forms.CharField(label="交易对象", max_length=200, required=False)
    note = forms.CharField(label="备注", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        assets = Account.objects.filter(balance_nature=Account.BalanceNature.ASSET, is_active=True)
        self.fields["source_account"].queryset = assets
        self.fields["destination_account"].queryset = assets

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("source_account") == cleaned_data.get("destination_account"):
            self.add_error("destination_account", "转出和转入账户不能相同。")
        return cleaned_data


class CreditCardRepaymentForm(forms.Form):
    amount = forms.DecimalField(label="还款金额", max_digits=14, decimal_places=2, min_value=0.01)
    occurred_at = BaseManualTransactionForm.base_fields["occurred_at"]
    source_account = forms.ModelChoiceField(label="资金来源账户", queryset=Account.objects.none())
    credit_card_account = forms.ModelChoiceField(
        label="信用卡账户", queryset=Account.objects.none()
    )
    channel = forms.ChoiceField(label="支付渠道", choices=Transaction.Channel.choices)
    note = forms.CharField(label="备注", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["source_account"].queryset = Account.objects.filter(
            balance_nature=Account.BalanceNature.ASSET, is_active=True
        )
        self.fields["credit_card_account"].queryset = Account.objects.filter(
            balance_nature=Account.BalanceNature.LIABILITY, is_active=True
        )


class BalanceAdjustmentForm(forms.Form):
    account = forms.ModelChoiceField(label="目标账户", queryset=Account.objects.none())
    balance_delta = forms.DecimalField(
        label="余额差额（可为负数）", max_digits=14, decimal_places=2
    )
    occurred_at = BaseManualTransactionForm.base_fields["occurred_at"]
    reason = forms.CharField(label="调整原因", max_length=500)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = Account.objects.filter(is_active=True)

    def clean_balance_delta(self):
        value = self.cleaned_data["balance_delta"]
        if value == 0:
            raise forms.ValidationError("余额差额不得为零。")
        return value


class TransactionFilterForm(forms.Form):
    date_from = forms.DateField(
        label="开始日期", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    date_to = forms.DateField(
        label="结束日期", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    transaction_type = forms.ChoiceField(
        label="类型", required=False, choices=[("", "全部"), *Transaction.TransactionType.choices]
    )
    account = forms.ModelChoiceField(label="账户", required=False, queryset=Account.objects.all())
    category = forms.ModelChoiceField(label="分类", required=False, queryset=Category.objects.all())
    amount_min = forms.DecimalField(
        label="最小金额", required=False, max_digits=14, decimal_places=2, min_value=0
    )
    amount_max = forms.DecimalField(
        label="最大金额", required=False, max_digits=14, decimal_places=2, min_value=0
    )
    keyword = forms.CharField(label="关键词", required=False, max_length=200)

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("date_from") and cleaned_data.get("date_to"):
            if cleaned_data["date_from"] > cleaned_data["date_to"]:
                self.add_error("date_to", "结束日期不得早于开始日期。")
        if (
            cleaned_data.get("amount_min") is not None
            and cleaned_data.get("amount_max") is not None
        ):
            if cleaned_data["amount_min"] > cleaned_data["amount_max"]:
                self.add_error("amount_max", "最大金额不得小于最小金额。")
        return cleaned_data


class VoidTransactionForm(forms.Form):
    reason = forms.CharField(label="作废原因", max_length=500)
