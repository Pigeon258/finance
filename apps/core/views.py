from django.contrib import auth, messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.sessions.models import Session
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.http import HttpRequest, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .forms import OwnerAuthenticationForm, SystemPreferenceForm
from .middleware import SESSION_CREATED_AT, SESSION_LAST_ACTIVITY_AT
from .models import SystemPreference
from .services import authenticate_with_throttle, get_session_limits


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
        has_unapplied_migrations = bool(
            executor.migration_plan(executor.loader.graph.leaf_nodes())
        )
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
    return render(request, "core/settings.html", {"form": form})
