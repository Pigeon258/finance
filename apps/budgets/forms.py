from decimal import Decimal

from django import forms

from apps.accounts.models import Account
from apps.core import selectors as core_selectors
from apps.ledger.models import Category

from .models import PlannedCashFlow, ReserveMovement


class MonthlyBudgetForm(forms.Form):
    month = forms.DateField(
        label="预算月份",
        input_formats=["%Y-%m"],
        widget=forms.DateInput(format="%Y-%m", attrs={"type": "month"}),
    )
    total_expense_budget = forms.DecimalField(
        label="月度总支出预算",
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.00"),
    )
    savings_target = forms.DecimalField(
        label="储蓄目标",
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.00"),
        initial=Decimal("0.00"),
    )
    minimum_safety_buffer = forms.DecimalField(
        label="最低安全余量",
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.00"),
        initial=Decimal("0.00"),
    )
    note = forms.CharField(label="备注", required=False, widget=forms.Textarea(attrs={"rows": 3}))


class CategoryBudgetForm(forms.Form):
    category = forms.ModelChoiceField(label="支出分类", queryset=Category.objects.none())
    budget_amount = forms.DecimalField(
        label="分类预算", max_digits=14, decimal_places=2, min_value=Decimal("0.00")
    )
    warning_threshold = forms.DecimalField(
        label="提醒阈值（%）",
        max_digits=5,
        decimal_places=2,
        min_value=Decimal("0.00"),
        max_value=Decimal("100.00"),
        initial=Decimal("80.00"),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        warning_threshold, over_threshold = core_selectors.budget_thresholds()
        self.fields["warning_threshold"].initial = warning_threshold
        self.fields["warning_threshold"].max_value = over_threshold
        self.fields["category"].queryset = Category.objects.filter(
            category_type=Category.CategoryType.EXPENSE, is_active=True
        )


class ReserveMovementForm(forms.Form):
    movement_type = forms.ChoiceField(
        label="变动类型", choices=ReserveMovement.MovementType.choices
    )
    amount = forms.DecimalField(label="金额", max_digits=14, decimal_places=2)
    occurred_on = forms.DateField(label="发生日期", widget=forms.DateInput(attrs={"type": "date"}))
    note = forms.CharField(label="说明", required=False, widget=forms.Textarea(attrs={"rows": 3}))


class PlannedCashFlowForm(forms.Form):
    name = forms.CharField(label="名称", max_length=200)
    direction = forms.ChoiceField(label="方向", choices=PlannedCashFlow.Direction.choices)
    amount = forms.DecimalField(
        label="计划金额", max_digits=14, decimal_places=2, min_value=Decimal("0.01")
    )
    category = forms.ModelChoiceField(label="分类", queryset=Category.objects.none())
    default_account = forms.ModelChoiceField(
        label="默认账户", queryset=Account.objects.none(), required=False
    )
    reliability = forms.ChoiceField(label="可靠程度", choices=PlannedCashFlow.Reliability.choices)
    recurrence_type = forms.ChoiceField(
        label="发生周期", choices=PlannedCashFlow.RecurrenceType.choices
    )
    start_date = forms.DateField(label="开始日期", widget=forms.DateInput(attrs={"type": "date"}))
    end_date = forms.DateField(
        label="结束日期", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    day_of_month = forms.IntegerField(label="每月日期", min_value=1, max_value=31, required=False)
    is_active = forms.BooleanField(label="启用", required=False, initial=True)
    note = forms.CharField(label="备注", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.filter(is_active=True)
        self.fields["default_account"].queryset = Account.objects.filter(is_active=True)


class GenerateOccurrencesForm(forms.Form):
    through_date = forms.DateField(label="生成至", widget=forms.DateInput(attrs={"type": "date"}))


class ConfirmOccurrenceForm(forms.Form):
    account = forms.ModelChoiceField(label="实际账户", queryset=Account.objects.none())
    actual_amount = forms.DecimalField(
        label="实际金额", max_digits=14, decimal_places=2, min_value=Decimal("0.01")
    )
    occurred_at = forms.DateTimeField(
        label="实际发生时间",
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}),
    )
    note = forms.CharField(label="说明", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = Account.objects.filter(is_active=True)
