from decimal import Decimal

from django import forms

from apps.accounts.models import Account
from apps.ledger.models import Category

from .models import WealthAccount


class WealthAccountForm(forms.ModelForm):
    class Meta:
        model = WealthAccount
        fields = [
            "name",
            "account_type",
            "institution",
            "fund_code",
            "auto_fetch_enabled",
            "is_active",
            "sort_order",
            "opened_on",
            "note",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["fund_code"].help_text = "余额宝固定填写 000198；其他产品可留空。"
        self.fields["auto_fetch_enabled"].help_text = "仅余额宝（000198）支持自动同步收益率。"


class TransferInForm(forms.Form):
    wealth_account = forms.ModelChoiceField(
        label="转入理财账户", queryset=WealthAccount.objects.none()
    )
    source_account = forms.ModelChoiceField(label="转出日常账户", queryset=Account.objects.none())
    amount = forms.DecimalField(
        label="转入金额", max_digits=14, decimal_places=2, min_value=Decimal("0.01")
    )
    occurred_at = forms.DateTimeField(
        label="发生时间",
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}),
    )
    note = forms.CharField(label="备注", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["wealth_account"].queryset = WealthAccount.objects.filter(is_active=True)
        self.fields["source_account"].queryset = Account.objects.filter(
            is_active=True,
            balance_nature=Account.BalanceNature.ASSET,
        ).exclude(account_type=Account.AccountType.WEALTH)


class TransferOutForm(forms.Form):
    wealth_account = forms.ModelChoiceField(
        label="转出理财账户", queryset=WealthAccount.objects.none()
    )
    destination_account = forms.ModelChoiceField(
        label="到账日常账户", queryset=Account.objects.none()
    )
    amount = forms.DecimalField(
        label="转出金额", max_digits=14, decimal_places=2, min_value=Decimal("0.01")
    )
    occurred_at = forms.DateTimeField(
        label="发生时间",
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}),
    )
    note = forms.CharField(label="备注", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["wealth_account"].queryset = WealthAccount.objects.filter(is_active=True)
        self.fields["destination_account"].queryset = Account.objects.filter(
            is_active=True,
            balance_nature=Account.BalanceNature.ASSET,
        ).exclude(account_type=Account.AccountType.WEALTH)


class ValuationForm(forms.Form):
    current_value = forms.DecimalField(
        label="当前市值", max_digits=14, decimal_places=2, min_value=Decimal("0.00")
    )
    valuation_date = forms.DateField(
        label="估值日期", widget=forms.DateInput(attrs={"type": "date"})
    )


class WealthIncomeForm(forms.Form):
    income_category = forms.ModelChoiceField(
        label="收入分类", queryset=Category.objects.none()
    )
    daily_account = forms.ModelChoiceField(
        label="到账日常账户",
        queryset=Account.objects.none(),
        required=False,
        help_text="收益实际到账到日常账户时选择；选择后会计入月度收入。",
    )
    amount = forms.DecimalField(
        label="收益金额", max_digits=14, decimal_places=2, min_value=Decimal("0.01")
    )
    occurred_on = forms.DateField(label="收益日期", widget=forms.DateInput(attrs={"type": "date"}))
    note = forms.CharField(label="备注", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["income_category"].queryset = Category.objects.filter(
            category_type=Category.CategoryType.INCOME, is_active=True
        )
        self.fields["daily_account"].queryset = Account.objects.filter(
            is_active=True,
            balance_nature=Account.BalanceNature.ASSET,
        ).exclude(account_type=Account.AccountType.WEALTH)
