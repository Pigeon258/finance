from django import forms

from apps.accounts.models import Account
from apps.ledger.forms import CreditCardExpenseForm, CreditCardRepaymentForm

from .models import CreditCardProfile


class CreditCardProfileForm(forms.ModelForm):
    class Meta:
        model = CreditCardProfile
        fields = [
            "account",
            "credit_limit",
            "personal_monthly_limit",
            "statement_day",
            "due_day",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = Account.objects.filter(
            balance_nature=Account.BalanceNature.LIABILITY, is_active=True
        )
        if self.instance.pk:
            self.fields["account"].disabled = True


class IssueCycleForm(forms.Form):
    official_statement_amount = forms.DecimalField(
        label="银行正式账单金额", max_digits=14, decimal_places=2, min_value=0
    )
    official_due_amount = forms.DecimalField(
        label="银行正式应还金额", max_digits=14, decimal_places=2, min_value=0
    )
    due_date = forms.DateField(label="本期还款日", widget=forms.DateInput(attrs={"type": "date"}))
    note = forms.CharField(label="备注", required=False, widget=forms.Textarea(attrs={"rows": 3}))


class CreditPurchaseForm(CreditCardExpenseForm):
    pass


class RepaymentForm(CreditCardRepaymentForm):
    pass
