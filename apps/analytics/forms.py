from decimal import Decimal

from django import forms


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
