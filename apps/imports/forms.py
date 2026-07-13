from pathlib import Path

from django import forms


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
