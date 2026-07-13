from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_superuser(
        username="owner", password="correct horse battery staple"
    )


@pytest.mark.django_db
def test_upcoming_view_uses_inclusive_thirty_day_window(client, owner):
    client.force_login(owner)
    today = date(2026, 7, 13)
    with (
        patch("apps.analytics.views.timezone.localdate", return_value=today),
        patch("apps.analytics.views.selectors.upcoming_items", return_value=()) as upcoming,
    ):
        response = client.get(reverse("analytics:upcoming"))

    assert response.status_code == 200
    assert response.context["as_of"] == today
    assert response.context["date_to"] == today + timedelta(days=30)
    upcoming.assert_called_once_with(as_of=today)
    assert "包含起止日期" in response.content.decode()
    assert "未来 30 天没有待办事项" in response.content.decode()


@pytest.mark.django_db
def test_navigation_and_settings_forms_work_without_javascript(client, owner):
    client.force_login(owner)

    response = client.get(reverse("core:settings"))
    body = response.content.decode()

    assert 'name="viewport"' in body
    assert 'class="skip-link"' in body
    assert reverse("analytics:upcoming") in body
    assert reverse("imports:rules") in body
    assert '<form method="post" action="' in body
    assert "hx-" not in body


def test_upcoming_view_requires_login(client):
    response = client.get(reverse("analytics:upcoming"))

    assert response.status_code == 302
    assert response.url.startswith(reverse("core:login"))
