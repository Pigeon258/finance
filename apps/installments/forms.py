from decimal import Decimal

from django import forms

from apps.accounts.models import Account
from apps.ledger.models import Category

from .models import InstallmentPlan


class PlanCreateForm(forms.Form):
    product_name = forms.CharField(label="商品名称", max_length=200)
    purchase_date = forms.DateField(
        label="购买日期", widget=forms.DateInput(attrs={"type": "date"})
    )
    original_price = forms.DecimalField(
        label="商品原价", max_digits=14, decimal_places=2, min_value=Decimal("0.01")
    )
    category = forms.ModelChoiceField(label="分类", queryset=Category.objects.none())
    source_type = forms.ChoiceField(label="分期来源", choices=InstallmentPlan.SourceType.choices)
    installment_count = forms.IntegerField(label="分期期数", min_value=1, max_value=600)
    default_installment_amount = forms.DecimalField(
        label="默认每期金额", max_digits=14, decimal_places=2, min_value=Decimal("0.01")
    )
    total_repayment_amount = forms.DecimalField(
        label="总还款金额",
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        required=False,
        help_text="留空时等于每期金额乘期数；填写后尾差计入最后一期。",
    )
    first_due_month = forms.DateField(
        label="信用卡首期月份",
        required=False,
        widget=forms.DateInput(format="%Y-%m", attrs={"type": "month"}),
        input_formats=["%Y-%m"],
    )
    first_due_date = forms.DateField(
        label="平台首期具体到期日",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    note = forms.CharField(label="备注", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.filter(
            category_type=Category.CategoryType.EXPENSE, is_active=True
        )

    def clean(self):
        cleaned = super().clean()
        source = cleaned.get("source_type")
        if source == InstallmentPlan.SourceType.CREDIT_CARD and not cleaned.get("first_due_month"):
            self.add_error("first_due_month", "信用卡分期必须选择首期月份。")
        if source == InstallmentPlan.SourceType.PLATFORM and not cleaned.get("first_due_date"):
            self.add_error("first_due_date", "平台分期必须填写首期具体到期日。")
        return cleaned


class ItemPostForm(forms.Form):
    actual_amount = forms.DecimalField(
        label="本期实际金额", max_digits=14, decimal_places=2, min_value=Decimal("0.01")
    )
    occurred_at = forms.DateTimeField(
        label="实际发生时间",
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}),
    )
    account = forms.ModelChoiceField(
        label="付款账户（平台分期）", queryset=Account.objects.none(), required=False
    )
    note = forms.CharField(label="备注", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = Account.objects.filter(
            balance_nature=Account.BalanceNature.ASSET, is_active=True
        )


class ItemAdjustForm(forms.Form):
    new_amount = forms.DecimalField(
        label="调整后金额", max_digits=14, decimal_places=2, min_value=Decimal("0.00")
    )
    effective_date = forms.DateField(
        label="生效日期", widget=forms.DateInput(attrs={"type": "date"})
    )
    new_due_date = forms.DateField(
        label="调整后预计到期日", widget=forms.DateInput(attrs={"type": "date"})
    )
    note = forms.CharField(
        label="调整说明", required=False, widget=forms.Textarea(attrs={"rows": 3})
    )


class EarlySettlementForm(forms.Form):
    amount = forms.DecimalField(
        label="实际结清金额", max_digits=14, decimal_places=2, min_value=Decimal("0.01")
    )
    occurred_at = ItemPostForm.base_fields["occurred_at"]
    account = forms.ModelChoiceField(
        label="付款账户（平台分期）", queryset=Account.objects.none(), required=False
    )
    note = forms.CharField(label="说明", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = Account.objects.filter(
            balance_nature=Account.BalanceNature.ASSET, is_active=True
        )


class PostedRefundForm(forms.Form):
    amount = forms.DecimalField(
        label="退款金额", max_digits=14, decimal_places=2, min_value=Decimal("0.01")
    )
    occurred_at = ItemPostForm.base_fields["occurred_at"]
    account = forms.ModelChoiceField(
        label="退款到账账户", queryset=Account.objects.none(), required=False
    )
    note = forms.CharField(label="说明", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = Account.objects.filter(is_active=True)
