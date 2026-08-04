import pytest
from django.contrib.auth import SESSION_KEY
from django.test import Client
from django.urls import reverse

from apps.core.forms import SystemPreferenceForm
from apps.core.models import SystemPreference

PASSWORD = "correct horse battery staple"


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_superuser(username="owner", password=PASSWORD)


def _preference_data(**overrides):
    data = {
        "time_zone": "Asia/Shanghai",
        "category_warning_threshold": "0.00",
        "category_over_budget_threshold": "100.00",
        "large_expense_threshold": "0.00",
        "login_failure_window_minutes": 1440,
        "login_failure_ip_limit": 10000,
        "login_failure_global_limit": 10000,
        "session_idle_timeout_minutes": 10080,
        "session_absolute_timeout_hours": 8760,
        "active_theme_id": "safe-default",
        "appearance_mode": SystemPreference.AppearanceMode.AUTO,
        "reduce_motion": False,
        "show_theme_background": True,
    }
    data.update(overrides)
    return data


@pytest.mark.django_db
def test_preference_threshold_boundaries_are_valid():
    preference = SystemPreference.objects.get()
    form = SystemPreferenceForm(_preference_data(), instance=preference)

    assert form.is_valid(), form.errors
    saved = form.save()
    assert str(saved.category_warning_threshold) == "0.00"
    assert str(saved.category_over_budget_threshold) == "100.00"
    assert str(saved.large_expense_threshold) == "0.00"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("category_warning_threshold", "100.01"),
        ("large_expense_threshold", "-0.01"),
        ("login_failure_window_minutes", 1441),
        ("login_failure_ip_limit", 10001),
        ("session_idle_timeout_minutes", 10081),
        ("session_absolute_timeout_hours", 8761),
    ],
)
def test_preference_thresholds_reject_values_outside_boundaries(field, value):
    preference = SystemPreference.objects.get()
    form = SystemPreferenceForm(_preference_data(**{field: value}), instance=preference)

    assert not form.is_valid()
    assert field in form.errors


@pytest.mark.django_db
def test_idle_timeout_cannot_exceed_absolute_timeout():
    preference = SystemPreference.objects.get()
    form = SystemPreferenceForm(
        _preference_data(session_idle_timeout_minutes=61, session_absolute_timeout_hours=1),
        instance=preference,
    )

    assert not form.is_valid()
    assert "空闲超时不得长于" in form.errors["session_idle_timeout_minutes"][0]


@pytest.mark.django_db
def test_owner_can_list_and_revoke_another_session_without_exposing_tokens(client, owner):
    other_client = Client()
    client.force_login(owner)
    other_client.force_login(owner)
    other_client.get(reverse("core:settings"))

    response = client.get(reverse("core:settings"))
    sessions = response.context["sessions"]
    other = next(item for item in sessions if not item.is_current)

    body = response.content.decode()
    assert client.session.session_key not in body
    assert other_client.session.session_key not in body
    assert "页面不会显示或记录会话令牌" in body

    revoked = client.post(reverse("core:session-revoke", args=[other.reference]))

    assert revoked.status_code == 302
    assert SESSION_KEY in client.session
    other_response = other_client.get(reverse("core:home"))
    assert other_response.status_code == 302
    assert other_response.url.startswith(reverse("core:login"))


@pytest.mark.django_db
def test_owner_can_revoke_current_session(client, owner):
    client.force_login(owner)
    response = client.get(reverse("core:settings"))
    current = next(item for item in response.context["sessions"] if item.is_current)

    revoked = client.post(reverse("core:session-revoke", args=[current.reference]))

    assert revoked.status_code == 302
    assert revoked.url == reverse("core:login")
    assert SESSION_KEY not in client.session


@pytest.mark.django_db
def test_owner_can_revoke_all_other_sessions(client, owner):
    other_clients = [Client(), Client()]
    client.force_login(owner)
    for other_client in other_clients:
        other_client.force_login(owner)

    response = client.post(reverse("core:sessions-revoke-others"))

    assert response.status_code == 302
    assert SESSION_KEY in client.session
    for other_client in other_clients:
        assert other_client.get(reverse("core:home")).url.startswith(reverse("core:login"))


@pytest.mark.django_db
def test_session_revocation_endpoints_require_post(client, owner):
    client.force_login(owner)
    response = client.get(reverse("core:settings"))
    reference = response.context["sessions"][0].reference

    assert client.get(reverse("core:session-revoke", args=[reference])).status_code == 405
    assert client.get(reverse("core:sessions-revoke-others")).status_code == 405
