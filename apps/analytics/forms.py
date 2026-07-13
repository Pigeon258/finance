from decimal import Decimal

from django import forms

from apps.accounts.models import Account
from apps.ledger.models import Category, Transaction


class MonthInput(forms.DateInput):
    input_type = "month"


class DashboardMonthForm(forms.Form):
    month = forms.DateField(
        label="统计月份", input_formats=["%Y-%m"], widget=MonthInput(format="%Y-%m")
    )


class ForecastForm(forms.Form):
    as_of = forms.DateField(label="计算日期", widget=forms.DateInput(attrs={"type": "date"}))
    month_count = forms.IntegerField(label="预测月数", min_value=1, max_value=120, initial=6)


class InstallmentPreviewForm(ForecastForm):
    first_month = forms.DateField(
        label="新增分期首期月份",
        input_formats=["%Y-%m"],
        widget=forms.DateInput(format="%Y-%m", attrs={"type": "month"}),
    )
    installment_count = forms.IntegerField(label="分期期数", min_value=1, max_value=600)
    installment_amount = forms.DecimalField(
        label="每期实际金额",
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )


class ReportFilterForm(forms.Form):
    date_from = forms.DateField(label="开始日期", widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(label="结束日期", widget=forms.DateInput(attrs={"type": "date"}))
    budget_month = forms.DateField(
        label="预算执行月份", input_formats=["%Y-%m"], widget=MonthInput(format="%Y-%m")
    )
    transaction_type = forms.ChoiceField(
        label="交易类型",
        required=False,
        choices=[("", "全部类型"), *Transaction.TransactionType.choices],
    )
    account = forms.ModelChoiceField(
        label="账户", required=False, queryset=Account.objects.none(), empty_label="全部账户"
    )
    category = forms.ModelChoiceField(
        label="分类", required=False, queryset=Category.objects.none(), empty_label="全部分类"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = Account.objects.order_by("sort_order", "id")
        self.fields["category"].queryset = Category.objects.order_by(
            "category_type", "sort_order", "id"
        )

    def clean(self):
        cleaned = super().clean()
        date_from = cleaned.get("date_from")
        date_to = cleaned.get("date_to")
        if date_from and date_to:
            if date_from > date_to:
                raise forms.ValidationError("开始日期不得晚于结束日期。")
            if (date_to - date_from).days > 366:
                raise forms.ValidationError("单次报表日期范围不得超过 367 天。")
        return cleaned
