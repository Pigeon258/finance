from pathlib import Path

from django import forms

from apps.accounts.models import Account
from apps.ledger.models import Category

from .models import ImportAccountRule, ImportDuplicateCandidate, ImportRecord, MerchantCategoryRule
from .normalization import normalize_key


class BillUploadForm(forms.Form):
    bill_file = forms.FileField(
        label="支付宝或微信账单文件",
        help_text="支持 CSV、XLSX 或包含一个受支持文件的 ZIP，最大 20 MB。",
        widget=forms.ClearableFileInput(attrs={"accept": ".csv,.xlsx,.zip"}),
    )

    def clean_bill_file(self):
        uploaded = self.cleaned_data["bill_file"]
        if Path(uploaded.name).suffix.lower() not in {".csv", ".xlsx", ".zip"}:
            raise forms.ValidationError("仅支持 CSV、XLSX 或 ZIP 文件。")
        return uploaded


class RecordReviewForm(forms.Form):
    mapped_account = forms.ModelChoiceField(
        label="实际账户", queryset=Account.objects.none(), required=False
    )
    selected_category = forms.ModelChoiceField(
        label="分类", queryset=Category.objects.none(), required=False
    )
    duplicate_resolution = forms.ChoiceField(
        label="重复处理",
        choices=[("", "未选择"), *ImportRecord.DuplicateResolution.choices],
        required=False,
    )
    selected_candidate = forms.ModelChoiceField(
        label="关联候选", queryset=ImportDuplicateCandidate.objects.none(), required=False
    )
    save_merchant_rule = forms.BooleanField(
        label="将该交易对方以后自动归入所选分类", required=False
    )

    def __init__(self, *args, record: ImportRecord, **kwargs):
        super().__init__(*args, **kwargs)
        self.record = record
        category_type = (
            Category.CategoryType.INCOME
            if record.candidate_transaction_type == ImportRecord.CandidateTransactionType.INCOME
            else Category.CategoryType.EXPENSE
        )
        self.fields["mapped_account"].queryset = Account.objects.filter(is_active=True)
        self.fields["selected_category"].queryset = Category.objects.filter(
            is_active=True, category_type=category_type
        )
        self.fields["selected_candidate"].queryset = record.duplicate_candidates.select_related(
            "transaction"
        )
        selected = record.duplicate_candidates.filter(is_selected=True).first()
        self.initial.update(
            {
                "mapped_account": record.mapped_account_id,
                "selected_category": record.selected_category_id,
                "duplicate_resolution": record.duplicate_resolution,
                "selected_candidate": selected.pk if selected else None,
            }
        )


class BulkReviewForm(forms.Form):
    class Action:
        SET_ACCOUNT = "SET_ACCOUNT"
        SET_CATEGORY = "SET_CATEGORY"
        IGNORE = "IGNORE"
        CONFIRM = "CONFIRM"

    record_ids = forms.TypedMultipleChoiceField(coerce=int, widget=forms.MultipleHiddenInput)
    action = forms.ChoiceField(
        choices=[
            (Action.SET_ACCOUNT, "批量修改账户"),
            (Action.SET_CATEGORY, "批量修改分类"),
            (Action.IGNORE, "批量忽略"),
            (Action.CONFIRM, "确认入账"),
        ]
    )
    account = forms.ModelChoiceField(queryset=Account.objects.none(), required=False)
    category = forms.ModelChoiceField(queryset=Category.objects.none(), required=False)

    def __init__(self, *args, available_ids=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["record_ids"].choices = [(value, str(value)) for value in available_ids]
        self.fields["account"].queryset = Account.objects.filter(is_active=True)
        self.fields["category"].queryset = Category.objects.filter(is_active=True)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("action") == self.Action.SET_ACCOUNT and not cleaned.get("account"):
            self.add_error("account", "批量修改账户时必须选择账户。")
        if cleaned.get("action") == self.Action.SET_CATEGORY and not cleaned.get("category"):
            self.add_error("category", "批量修改分类时必须选择分类。")
        return cleaned


class MerchantCategoryRuleForm(forms.ModelForm):
    class Meta:
        model = MerchantCategoryRule
        fields = [
            "name",
            "match_target",
            "match_kind",
            "pattern",
            "category",
            "priority",
            "is_active",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.filter(is_active=True)

    def clean_pattern(self):
        pattern = normalize_key(self.cleaned_data["pattern"])
        if not pattern:
            raise forms.ValidationError("匹配内容不能为空。")
        return pattern


class ImportAccountRuleForm(forms.ModelForm):
    class Meta:
        model = ImportAccountRule
        fields = ["name", "source", "match_kind", "pattern", "account", "priority", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = Account.objects.filter(is_active=True)

    def clean_pattern(self):
        pattern = normalize_key(self.cleaned_data["pattern"])
        if not pattern:
            raise forms.ValidationError("匹配内容不能为空。")
        return pattern
