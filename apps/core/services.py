from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.models import AbstractBaseUser
from django.db import DatabaseError, transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .models import LoginAttempt, SystemPreference

LOGIN_ATTEMPT_RETENTION_DAYS = 30


@dataclass(frozen=True)
class AuthenticationResult:
    user: AbstractBaseUser | None
    blocked: bool


def hash_source_ip(source_ip: str) -> str:
    normalized_ip = source_ip.strip() or "unknown"
    return salted_hmac(
        "personal-finance.login-ip", normalized_ip, algorithm="sha256"
    ).hexdigest()


def get_source_ip(meta: dict[str, str]) -> str:
    # The reverse proxy trust boundary will be configured in the deployment task.
    # Reading X-Forwarded-For here would allow clients to choose their own rate-limit key.
    return meta.get("REMOTE_ADDR", "unknown")


@transaction.atomic
def authenticate_with_throttle(
    *, request, username: str, password: str, now=None
) -> AuthenticationResult:
    now = now or timezone.now()
    preference, _ = SystemPreference.objects.select_for_update().get_or_create(
        pk=SystemPreference.SINGLETON_ID
    )
    window_start = now - timedelta(minutes=preference.login_failure_window_minutes)
    ip_hash = hash_source_ip(get_source_ip(request.META))
    failures = LoginAttempt.objects.filter(succeeded=False, occurred_at__gte=window_start)

    if (
        failures.filter(ip_hash=ip_hash).count() >= preference.login_failure_ip_limit
        or failures.count() >= preference.login_failure_global_limit
    ):
        return AuthenticationResult(user=None, blocked=True)

    user = authenticate(request=request, username=username, password=password)
    succeeded = bool(user and user.is_active and user.is_superuser)
    LoginAttempt.objects.create(ip_hash=ip_hash, succeeded=succeeded)
    LoginAttempt.objects.filter(
        occurred_at__lt=now - timedelta(days=LOGIN_ATTEMPT_RETENTION_DAYS)
    ).delete()

    if not succeeded:
        return AuthenticationResult(user=None, blocked=False)
    return AuthenticationResult(user=user, blocked=False)


def get_session_limits() -> tuple[int, int]:
    try:
        preference = SystemPreference.objects.get(pk=SystemPreference.SINGLETON_ID)
        return (
            preference.session_idle_timeout_minutes * 60,
            preference.session_absolute_timeout_hours * 60 * 60,
        )
    except (SystemPreference.DoesNotExist, DatabaseError):
        return (
            settings.SESSION_IDLE_TIMEOUT_MINUTES * 60,
            settings.SESSION_ABSOLUTE_TIMEOUT_HOURS * 60 * 60,
        )


def get_display_time_zone() -> str:
    try:
        return SystemPreference.objects.values_list("time_zone", flat=True).get(
            pk=SystemPreference.SINGLETON_ID
        )
    except (SystemPreference.DoesNotExist, DatabaseError):
        return settings.TIME_ZONE
