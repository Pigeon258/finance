from django import forms

from .models import Account


class AccountForm(forms.ModelForm):
    class Meta:
        model = Account
        fields = [
            "name",
            "account_type",
            "initial_balance",
            "is_active",
            "sort_order",
            "opened_at",
            "note",
        ]
        widgets = {"opened_at": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["account_type"].disabled = True
            self.fields["account_type"].help_text = "账户创建后不能修改类型。"
