from datetime import timedelta
from unittest.mock import patch

import pytest
from django.conf import settings
from django.contrib.auth import SESSION_KEY
from django.contrib.sessions.models import Session
from django.test import override_settings
from django.urls import Resolver404, resolve, reverse
from django.utils import timezone

from apps.core.middleware import SESSION_CREATED_AT, SESSION_LAST_ACTIVITY_AT
from apps.core.models import LoginAttempt, SystemPreference

PASSWORD = "correct horse battery staple"


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_superuser(username="owner", password=PASSWORD)


@pytest.mark.django_db
def test_financial_pages_require_login(client):
    response = client.get(reverse("core:home"))

    assert response.status_code == 302
    assert response.url.startswith(reverse("core:login"))


def test_health_checks_and_login_are_public(client):
    assert client.get(reverse("core:health-live")).status_code == 200
    assert client.get(reverse("core:login")).status_code == 200


def test_no_registration_route_exists():
    with pytest.raises(Resolver404):
        resolve("/register/")


@pytest.mark.django_db
def test_successful_login_records_hashed_source_and_initializes_session(client, owner):
    response = client.post(
        reverse("core:login"),
        {"username": owner.username, "password": PASSWORD},
        REMOTE_ADDR="192.0.2.10",
    )

    assert response.status_code == 302
    assert response.url == reverse("core:home")
    assert SESSION_KEY in client.session
    assert SESSION_CREATED_AT in client.session
    assert SESSION_LAST_ACTIVITY_AT in client.session
    attempt = LoginAttempt.objects.get()
    assert attempt.succeeded is True
    assert attempt.ip_hash != "192.0.2.10"
    assert len(attempt.ip_hash) == 64


@pytest.mark.django_db
def test_non_owner_user_cannot_log_in(client, django_user_model):
    django_user_model.objects.create_user(username="not-owner", password=PASSWORD)

    response = client.post(
        reverse("core:login"), {"username": "not-owner", "password": PASSWORD}
    )

    assert response.status_code == 200
    assert SESSION_KEY not in client.session
    assert LoginAttempt.objects.get().succeeded is False


@pytest.mark.django_db
def test_per_ip_failure_limit_blocks_further_authentication(client, owner):
    login_url = reverse("core:login")
    for _ in range(5):
        response = client.post(
            login_url,
            {"username": owner.username, "password": "wrong-password"},
            REMOTE_ADDR="192.0.2.20",
        )
        assert "用户名或密码错误" in response.content.decode()

    blocked = client.post(
        login_url,
        {"username": owner.username, "password": PASSWORD},
        REMOTE_ADDR="192.0.2.20",
    )

    assert "登录尝试过多" in blocked.content.decode()
    assert SESSION_KEY not in client.session
    assert LoginAttempt.objects.count() == 5


@pytest.mark.django_db
def test_global_failure_limit_blocks_other_ip(client, owner):
    preference = SystemPreference.objects.get()
    preference.login_failure_ip_limit = 2
    preference.login_failure_global_limit = 2
    preference.save()
    login_url = reverse("core:login")
    for index in range(2):
        client.post(
            login_url,
            {"username": owner.username, "password": "wrong-password"},
            REMOTE_ADDR=f"192.0.2.{index}",
        )

    response = client.post(
        login_url,
        {"username": owner.username, "password": PASSWORD},
        REMOTE_ADDR="198.51.100.1",
    )

    assert "登录尝试过多" in response.content.decode()
    assert SESSION_KEY not in client.session


@pytest.mark.django_db
def test_expired_failures_do_not_count_toward_limit(client, owner):
    client.post(
        reverse("core:login"),
        {"username": owner.username, "password": "wrong-password"},
        REMOTE_ADDR="192.0.2.30",
    )
    LoginAttempt.objects.update(occurred_at=timezone.now() - timedelta(minutes=16))

    response = client.post(
        reverse("core:login"),
        {"username": owner.username, "password": PASSWORD},
        REMOTE_ADDR="192.0.2.30",
    )

    assert response.status_code == 302
    assert SESSION_KEY in client.session


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("created_age", "activity_age"),
    [(timedelta(hours=25), timedelta(minutes=1)), (timedelta(hours=1), timedelta(minutes=61))],
)
def test_expired_session_is_logged_out(client, owner, created_age, activity_age):
    client.force_login(owner)
    now = timezone.now()
    session = client.session
    session[SESSION_CREATED_AT] = int((now - created_age).timestamp())
    session[SESSION_LAST_ACTIVITY_AT] = int((now - activity_age).timestamp())
    session.save()

    with patch("apps.core.middleware.timezone.now", return_value=now):
        response = client.get(reverse("core:home"))

    assert response.status_code == 302
    assert response.url == reverse("core:login")
    assert SESSION_KEY not in client.session


@pytest.mark.django_db
def test_logout_requires_post_and_deletes_current_session(client, owner):
    client.force_login(owner)

    assert client.get(reverse("core:logout")).status_code == 405
    response = client.post(reverse("core:logout"))

    assert response.status_code == 302
    assert SESSION_KEY not in client.session


@pytest.mark.django_db
def test_password_change_invalidates_other_sessions(client, owner):
    other_client = client.__class__()
    client.force_login(owner)
    other_client.force_login(owner)
    assert Session.objects.count() == 2

    response = client.post(
        reverse("core:password-change"),
        {
            "old_password": PASSWORD,
            "new_password1": "a newer correct horse battery staple",
            "new_password2": "a newer correct horse battery staple",
        },
    )

    assert response.status_code == 302
    assert SESSION_KEY in client.session
    assert other_client.get(reverse("core:home")).url.startswith(reverse("core:login"))


@pytest.mark.django_db
def test_settings_validate_timezone_and_threshold_order(client, owner):
    client.force_login(owner)
    preference = SystemPreference.objects.get()
    response = client.post(
        reverse("core:settings"),
        {
            "time_zone": "Not/AZone",
            "category_warning_threshold": "90.00",
            "category_over_budget_threshold": "80.00",
            "large_expense_threshold": "500.00",
            "login_failure_window_minutes": 15,
            "login_failure_ip_limit": 5,
            "login_failure_global_limit": 20,
            "session_idle_timeout_minutes": 60,
            "session_absolute_timeout_hours": 24,
        },
    )

    assert response.status_code == 200
    assert "有效的 IANA 时区" in response.content.decode()
    preference.refresh_from_db()
    assert preference.time_zone == "Asia/Shanghai"


@override_settings(PASSWORD_HASHERS=settings.PASSWORD_HASHERS)
@pytest.mark.django_db
def test_owner_password_uses_argon2(owner):
    assert owner.password.startswith("argon2$")
    assert settings.PASSWORD_HASHERS[:2] == [
        "django.contrib.auth.hashers.Argon2PasswordHasher",
        "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    ]
