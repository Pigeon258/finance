import io
import time

from django.conf import settings
from django.contrib import auth, messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.sessions.models import Session
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.http import FileResponse, HttpRequest, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from . import selectors, services
from .backup import BackupError, create_user_backup, restore_business_backup
from .exports import monthly_statistics_csv, transaction_csv
from .forms import (
    BackupDownloadForm,
    BackupRestoreForm,
    ExportRangeForm,
    OwnerAuthenticationForm,
    SystemPreferenceForm,
    ThemeUploadForm,
)
from .middleware import SESSION_CREATED_AT, SESSION_LAST_ACTIVITY_AT
from .models import BackupRun, SystemPreference
from .services import authenticate_with_throttle, get_session_limits
from .theme_library import (
    ThemeLibraryError,
    activate_theme,
    delete_theme,
    install_theme_zip,
    restore_safe_default,
)
from .themes import get_theme_registry


@require_GET
def home(request: HttpRequest):
    return render(request, "core/home.html")


@require_GET
def health_live(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


@require_GET
def health_ready(request: HttpRequest) -> JsonResponse:
    connection = connections["default"]
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        executor = MigrationExecutor(connection)
        has_unapplied_migrations = bool(executor.migration_plan(executor.loader.graph.leaf_nodes()))
    except Exception:  # Health checks must not expose database or migration details.
        return JsonResponse({"status": "unavailable"}, status=503)

    if has_unapplied_migrations:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


@require_http_methods(["GET", "POST"])
def login_view(request: HttpRequest):
    if request.user.is_authenticated:
        return redirect("core:home")

    form = OwnerAuthenticationForm(request=request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        result = authenticate_with_throttle(
            request=request,
            username=form.cleaned_data["username"],
            password=form.cleaned_data["password"],
        )
        if result.blocked:
            form.add_error(None, "登录尝试过多，请稍后再试。")
        elif result.user is None:
            form.add_error(None, "用户名或密码错误。")
        else:
            auth.login(request, result.user)
            now_timestamp = int(timezone.now().timestamp())
            request.session[SESSION_CREATED_AT] = now_timestamp
            request.session[SESSION_LAST_ACTIVITY_AT] = now_timestamp
            _, absolute_session_limit = get_session_limits()
            request.session.set_expiry(absolute_session_limit)
            next_url = request.POST.get("next")
            if next_url and url_has_allowed_host_and_scheme(
                next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
            ):
                return redirect(next_url)
            return redirect("core:home")

    return render(request, "registration/login.html", {"form": form})


@require_POST
def logout_view(request: HttpRequest):
    auth.logout(request)
    return redirect("core:login")


@require_http_methods(["GET", "POST"])
def password_change_view(request: HttpRequest):
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        current_session_key = request.session.session_key
        Session.objects.filter(expire_date__gt=timezone.now()).exclude(
            session_key=current_session_key
        ).delete()
        messages.success(request, "密码已修改，其他会话已退出。")
        return redirect("core:settings")
    return render(request, "registration/password_change_form.html", {"form": form})


@require_http_methods(["GET", "POST"])
def settings_view(request: HttpRequest):
    preference, _ = SystemPreference.objects.get_or_create(pk=SystemPreference.SINGLETON_ID)
    form = SystemPreferenceForm(request.POST or None, instance=preference)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "系统设置已保存。")
        return redirect(reverse("core:settings"))
    current_session_key = request.session.session_key or ""
    sessions = selectors.active_owner_sessions(
        owner_id=request.user.pk, current_session_key=current_session_key
    )
    return render(request, "core/settings.html", {"form": form, "sessions": sessions})


@require_http_methods(["GET", "POST"])
def theme_library_view(request: HttpRequest):
    form = ThemeUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            result = install_theme_zip(form.cleaned_data["theme_zip"])
        except ThemeLibraryError as error:
            form.add_error("theme_zip", str(error))
        else:
            if result.status == "already-installed":
                messages.info(request, "相同主题已经安装，未重复写入。")
            else:
                messages.success(request, f"主题“{result.theme.name}”已安全导入，尚未启用。")
            return redirect("core:theme-library")

    preference, _ = SystemPreference.objects.get_or_create(pk=SystemPreference.SINGLETON_ID)
    registry = get_theme_registry()
    preview = request.session.get("theme_preview", {})
    preview_id = preview.get("id", "") if isinstance(preview, dict) else ""
    return render(
        request,
        "core/theme_library.html",
        {
            "active_theme_id": preference.active_theme_id,
            "form": form,
            "last_known_good_theme_id": preference.last_known_good_theme_id,
            "preview_theme_id": preview_id,
            "registry_errors": registry.errors,
            "themes": registry.themes,
        },
    )


@require_POST
def theme_preview(request: HttpRequest, theme_id: str):
    theme = get_theme_registry().get(theme_id)
    if theme is None:
        messages.error(request, "主题当前不可用，无法预览。")
        return redirect("core:theme-library")
    request.session["theme_preview"] = {
        "expires_at": int(time.time()) + settings.THEME_PREVIEW_TTL_SECONDS,
        "id": theme.id,
    }
    messages.info(request, f"正在临时预览“{theme.name}”；预览不会自动启用。")
    return redirect("core:home")


@require_POST
def theme_preview_clear(request: HttpRequest):
    request.session.pop("theme_preview", None)
    messages.info(request, "主题预览已结束。")
    return redirect("core:theme-library")


@require_POST
def theme_activate(request: HttpRequest, theme_id: str):
    try:
        theme = activate_theme(theme_id)
    except ThemeLibraryError as error:
        messages.error(request, str(error))
    else:
        request.session.pop("theme_preview", None)
        messages.success(request, f"已启用主题“{theme.name}”，并更新 last-known-good。")
    return redirect("core:theme-library")


@require_POST
def theme_restore_safe(request: HttpRequest):
    restore_safe_default()
    request.session.pop("theme_preview", None)
    messages.success(request, "已恢复安全默认主题。")
    return redirect("core:theme-library")


@require_POST
def theme_delete(request: HttpRequest, theme_id: str):
    preview = request.session.get("theme_preview", {})
    preview_id = preview.get("id", "") if isinstance(preview, dict) else ""
    try:
        delete_theme(theme_id, preview_theme_id=preview_id)
    except ThemeLibraryError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "运行时主题已删除。")
    return redirect("core:theme-library")


@require_POST
def session_revoke(request: HttpRequest, reference: str):
    try:
        is_current = services.revoke_owner_session(
            owner_id=request.user.pk,
            reference=reference,
            current_session_key=request.session.session_key or "",
        )
    except ValueError as error:
        messages.error(request, str(error))
        return redirect("core:settings")
    if is_current:
        auth.logout(request)
        return redirect("core:login")
    messages.success(request, "所选会话已撤销。")
    return redirect("core:settings")


@require_POST
def sessions_revoke_others(request: HttpRequest):
    count = services.revoke_other_owner_sessions(
        owner_id=request.user.pk, current_session_key=request.session.session_key or ""
    )
    messages.success(request, f"已撤销 {count} 个其他会话。")
    return redirect("core:settings")


def _export_context(*, range_form=None, backup_form=None, restore_form=None):
    return {
        "range_form": range_form or ExportRangeForm(),
        "backup_form": backup_form or BackupDownloadForm(),
        "restore_form": restore_form or BackupRestoreForm(),
        "backup_runs": BackupRun.objects.all()[:20],
    }


@require_GET
def export_center(request: HttpRequest):
    return render(request, "core/export_center.html", _export_context())


@require_GET
def export_transactions_csv(request: HttpRequest):
    form = ExportRangeForm(request.GET)
    if not form.is_valid():
        return render(
            request,
            "core/export_center.html",
            _export_context(range_form=form),
            status=400,
        )
    return transaction_csv(**form.cleaned_data)


@require_GET
def export_monthly_statistics_csv(request: HttpRequest):
    form = ExportRangeForm(request.GET)
    if not form.is_valid():
        return render(
            request,
            "core/export_center.html",
            _export_context(range_form=form),
            status=400,
        )
    return monthly_statistics_csv(**form.cleaned_data)


@require_POST
def backup_download(request: HttpRequest):
    form = BackupDownloadForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "core/export_center.html",
            _export_context(backup_form=form),
            status=400,
        )
    try:
        file_bytes, filename = create_user_backup(form.cleaned_data["backup_passphrase"])
    except BackupError as error:
        form.add_error(None, str(error))
        return render(
            request,
            "core/export_center.html",
            _export_context(backup_form=form),
            status=400,
        )
    response = FileResponse(
        io.BytesIO(file_bytes),
        as_attachment=True,
        filename=filename,
        content_type="application/octet-stream",
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "no-store"
    return response


@require_POST
def backup_restore(request: HttpRequest):
    form = BackupRestoreForm(request.POST, request.FILES)
    if form.is_valid() and not request.user.check_password(form.cleaned_data["system_password"]):
        form.add_error("system_password", "当前系统密码不正确。")
    if not form.is_valid():
        return render(
            request,
            "core/export_center.html",
            _export_context(restore_form=form),
            status=400,
        )
    uploaded = form.cleaned_data["backup_file"]
    file_bytes = uploaded.read()
    try:
        restore_business_backup(
            file_bytes,
            form.cleaned_data["backup_passphrase"],
            uploaded_filename=uploaded.name,
        )
    except BackupError as error:
        form.add_error(None, str(error))
    except Exception:
        form.add_error(None, "恢复失败，原业务数据未改变。请检查备份文件后重试。")
    if form.errors:
        return render(
            request,
            "core/export_center.html",
            _export_context(restore_form=form),
            status=400,
        )
    auth.logout(request)
    return redirect(f"{reverse('core:login')}?restored=1")
