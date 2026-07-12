from unittest.mock import patch

import pytest
from django.db import DatabaseError, connections
from django.urls import reverse


def test_home_renders_base_template(client):
    response = client.get(reverse("core:home"))

    assert response.status_code == 200
    assert "个人财务管理系统" in response.content.decode()


@pytest.mark.django_db
def test_live_health_check_does_not_query_database(client, django_assert_num_queries):
    with django_assert_num_queries(0):
        response = client.get(reverse("core:health-live"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_ready_health_check_reports_ok(client):
    response = client.get(reverse("core:health-ready"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_ready_health_check_hides_database_failure(client):
    connection = connections["default"]
    with patch.object(connection, "cursor", side_effect=DatabaseError):
        response = client.get(reverse("core:health-ready"))

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert set(response.json()) == {"status"}


def test_financial_test_amounts_must_use_decimal_strings(decimal_amount):
    assert str(decimal_amount("0.01")) == "0.01"

    with pytest.raises(TypeError):
        decimal_amount(0.01)
