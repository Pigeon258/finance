import csv
import io
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse

from apps.accounts.models import Account
from apps.ledger import services as ledger_services
from apps.ledger.models import Category, Transaction

TZ = ZoneInfo("Asia/Shanghai")


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_superuser(
        username="owner", password="correct horse battery staple"
    )


def _stream_text(response) -> str:
    return b"".join(response.streaming_content).decode("utf-8-sig")


@pytest.mark.django_db
def test_transaction_csv_is_utf8_exact_and_formula_safe(client, owner):
    client.force_login(owner)
    bank = Account.objects.get(account_type=Account.AccountType.BANK)
    bank.name = "=危险账户"
    bank.save(update_fields=["name", "updated_at"])
    category = Category.objects.get(name="餐饮")
    ledger_services.create_expense(
        account=bank,
        category=category,
        amount=Decimal("12.34"),
        occurred_at=datetime(2026, 7, 13, 1, 2, tzinfo=ZoneInfo("UTC")),
        channel=Transaction.Channel.BANK,
        counterparty="=SUM(1,1)\n中文商家",
        note="@恶意公式",
    )

    response = client.get(
        reverse("core:transactions-csv"),
        {"date_from": "2026-07-01", "date_to": "2026-07-31"},
    )
    rows = list(csv.reader(io.StringIO(_stream_text(response))))

    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv; charset=utf-8"
    assert rows[1][4] == "12.34"
    assert rows[1][8] == "'=SUM(1,1)\n中文商家"
    assert rows[1][9] == "'@恶意公式"
    assert "'=危险账户: -12.34" in rows[1][11]
    assert rows[1][1].endswith("+08:00")


@pytest.mark.django_db
def test_monthly_statistics_csv_contains_decimal_values(client, owner):
    client.force_login(owner)
    bank = Account.objects.get(account_type=Account.AccountType.BANK)
    category = Category.objects.get(name="其他收入")
    ledger_services.create_income(
        account=bank,
        category=category,
        amount=Decimal("88.09"),
        occurred_at=datetime(2026, 7, 13, 12, 0, tzinfo=TZ),
        channel=Transaction.Channel.BANK,
    )

    response = client.get(
        reverse("core:monthly-statistics-csv"),
        {"date_from": "2026-07-01", "date_to": "2026-07-31"},
    )
    rows = list(csv.reader(io.StringIO(_stream_text(response))))

    assert rows == [["月份", "收入", "净支出"], ["2026-07", "88.09", "0.00"]]


@pytest.mark.django_db
def test_csv_range_validation_uses_regular_html_without_javascript(client, owner):
    client.force_login(owner)

    response = client.get(
        reverse("core:transactions-csv"),
        {"date_from": "2026-08-01", "date_to": "2026-07-01"},
    )

    assert response.status_code == 400
    assert "结束日期不得早于开始日期" in response.content.decode()
    assert "hx-" not in response.content.decode()
