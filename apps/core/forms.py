from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import forms
from django.conf import settings
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
            "active_theme_id",
            "appearance_mode",
            "reduce_motion",
            "show_theme_background",
        ]

        labels = {
            "time_zone": "系统时区",
            "category_warning_threshold": "分类预算提醒阈值（%）",
            "category_over_budget_threshold": "分类预算超支阈值（%）",
            "large_expense_threshold": "大额支出阈值",
            "login_failure_window_minutes": "登录失败统计窗口（分钟）",
            "login_failure_ip_limit": "单个来源登录失败上限",
            "login_failure_global_limit": "全局登录失败上限",
            "session_idle_timeout_minutes": "会话空闲超时（分钟）",
            "session_absolute_timeout_hours": "会话最长时间（小时）",
            "active_theme_id": "界面主题",
            "appearance_mode": "明暗外观",
            "reduce_motion": "减少界面动效",
            "show_theme_background": "显示主题背景",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 主题注册器只暴露已经完整校验的主题，避免设置页保存未知 ID。
        from .themes import get_theme_registry

        registry = get_theme_registry()
        choices = [(theme.id, f"{theme.name}（{theme.version}）") for theme in registry.themes]
        current_id = getattr(self.instance, "active_theme_id", "")
        if current_id and current_id not in {value for value, _ in choices}:
            choices.append((current_id, f"{current_id}（当前未安装，将安全回退）"))
        self.fields["active_theme_id"].widget = forms.Select(choices=choices)

    def clean_active_theme_id(self):
        value = self.cleaned_data["active_theme_id"]
        from .themes import get_theme_registry

        if get_theme_registry().get(value) is None:
            raise forms.ValidationError("所选主题当前不可用，请先重新安装或选择安全默认主题。")
        return value

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
        idle_minutes = cleaned_data.get("session_idle_timeout_minutes")
        absolute_hours = cleaned_data.get("session_absolute_timeout_hours")
        if (
            idle_minutes is not None
            and absolute_hours is not None
            and idle_minutes > absolute_hours * 60
        ):
            self.add_error("session_idle_timeout_minutes", "空闲超时不得长于会话最长时间。")
        return cleaned_data


class ExportRangeForm(forms.Form):
    date_from = forms.DateField(label="开始日期", widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(label="结束日期", widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, **kwargs):
        if not args and "data" not in kwargs and "initial" not in kwargs:
            today = date.today()
            kwargs["initial"] = {"date_from": today.replace(month=1, day=1), "date_to": today}
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        date_from = cleaned_data.get("date_from")
        date_to = cleaned_data.get("date_to")
        if date_from and date_to and date_from > date_to:
            self.add_error("date_to", "结束日期不得早于开始日期。")
        return cleaned_data


class BackupDownloadForm(forms.Form):
    backup_passphrase = forms.CharField(
        label="备份口令",
        min_length=12,
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    backup_passphrase_confirm = forms.CharField(
        label="确认备份口令",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("backup_passphrase") != cleaned_data.get("backup_passphrase_confirm"):
            self.add_error("backup_passphrase_confirm", "两次输入的备份口令不一致。")
        return cleaned_data


class BackupRestoreForm(forms.Form):
    system_password = forms.CharField(
        label="当前系统密码",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )
    backup_file = forms.FileField(label="加密业务备份")
    backup_passphrase = forms.CharField(
        label="备份口令",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "off"}),
    )
    confirm_restore = forms.BooleanField(
        label="我确认恢复将替换当前业务数据，并已妥善保存恢复前自动备份所用口令。"
    )

    def clean_backup_file(self):
        uploaded = self.cleaned_data["backup_file"]
        if uploaded.size > settings.BUSINESS_BACKUP_MAX_UPLOAD_BYTES:
            raise forms.ValidationError("备份文件超过允许大小。")
        if not uploaded.name.lower().endswith(".pfbackup"):
            raise forms.ValidationError("请选择 .pfbackup 文件。")
        return uploaded
