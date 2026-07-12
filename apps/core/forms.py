from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import forms
from django.contrib.auth.forms import AuthenticationForm

from .models import SystemPreference


class OwnerAuthenticationForm(AuthenticationForm):
    username = forms.CharField(
        label="用户名", max_length=150, widget=forms.TextInput(attrs={"autofocus": True})
    )
    password = forms.CharField(label="密码", strip=False, widget=forms.PasswordInput)

    def clean(self):
        # Authentication is performed by the throttled service in the view.
        return forms.Form.clean(self)


class SystemPreferenceForm(forms.ModelForm):
    class Meta:
        model = SystemPreference
        fields = [
            "time_zone",
            "category_warning_threshold",
            "category_over_budget_threshold",
            "large_expense_threshold",
            "login_failure_window_minutes",
            "login_failure_ip_limit",
            "login_failure_global_limit",
            "session_idle_timeout_minutes",
            "session_absolute_timeout_hours",
        ]

    def clean_time_zone(self):
        value = self.cleaned_data["time_zone"]
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise forms.ValidationError("请输入有效的 IANA 时区名称。") from error
        return value

    def clean(self):
        cleaned_data = super().clean()
        warning = cleaned_data.get("category_warning_threshold")
        over_budget = cleaned_data.get("category_over_budget_threshold")
        if warning is not None and over_budget is not None and warning > over_budget:
            self.add_error("category_warning_threshold", "提醒阈值不得高于超支阈值。")
        ip_limit = cleaned_data.get("login_failure_ip_limit")
        global_limit = cleaned_data.get("login_failure_global_limit")
        if ip_limit is not None and global_limit is not None and ip_limit > global_limit:
            self.add_error("login_failure_ip_limit", "单个来源限制不得高于全局限制。")
        return cleaned_data
