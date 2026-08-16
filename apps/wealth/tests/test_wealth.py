from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse

from apps.accounts.models import Account
from apps.ledger import selectors as ledger_selectors
from apps.ledger.models import Category
from apps.wealth import selectors, services
from apps.wealth.models import WealthAccount, WealthFlow

TZ = ZoneInfo("Asia/Shanghai")
PASSWORD = "correct horse battery staple"


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_superuser(username="owner", password=PASSWORD)


@pytest.fixture
def authenticated_client(client, owner):
    client.force_login(owner)
    return client


@pytest.fixture
def bank():
    account = Account.objects.get(account_type=Account.AccountType.BANK)
    account.initial_balance = Decimal("2000.00")
    account.save(update_fields=["initial_balance", "updated_at"])
    return account


@pytest.fixture
def wealth_account():
    return services.create_wealth_account(
        name="余额宝",
        account_type=WealthAccount.AccountType.MONEY_FUND,
        institution="支付宝",
        fund_code="000198",
        auto_fetch_enabled=True,
    )


@pytest.mark.django_db
def test_wealth_pages_require_login(client):
    assert client.get(reverse("wealth:overview")).status_code == 302


@pytest.mark.django_db
def test_create_account_and_exclude_from_liquid_assets(authenticated_client, wealth_account):
    core = wealth_account.core_account
    assert core.account_type == Account.AccountType.WEALTH
    assert ledger_selectors.account_balance(account=core) == Decimal("0.00")
    assert Account.objects.filter(account_type=Account.AccountType.WEALTH).count() == 1
    assert ledger_selectors.liquid_assets() == Decimal("0.00")


@pytest.mark.django_db
def test_transfer_in_changes_daily_and_wealth_balances_but_not_income(
    authenticated_client, bank, wealth_account
):
    services.transfer_in(
        wealth_account=wealth_account,
        source_account=bank,
        amount=Decimal("500.00"),
        occurred_at=datetime(2026, 7, 1, 12, 0, tzinfo=TZ),
    )

    assert ledger_selectors.account_balance(account=bank) == Decimal("1500.00")
    assert (
        ledger_selectors.account_balance(account=wealth_account.core_account)
        == Decimal("500.00")
    )
    assert selectors.total_value() == Decimal("500.00")
    assert ledger_selectors.monthly_income(month=date(2026, 7, 1)) == Decimal("0.00")
    assert WealthFlow.objects.get().flow_type == WealthFlow.FlowType.TRANSFER_IN


@pytest.mark.django_db
def test_income_stays_in_wealth_or_arrives_daily(
    authenticated_client, bank, wealth_account
):
    category = Category.objects.get(category_type=Category.CategoryType.INCOME)
    services.transfer_in(
        wealth_account=wealth_account,
        source_account=bank,
        amount=Decimal("500.00"),
        occurred_at=datetime(2026, 7, 1, 12, 0, tzinfo=TZ),
    )

    services.record_income(
        wealth_account=wealth_account,
        income_category=category,
        amount=Decimal("2.00"),
        occurred_on=date(2026, 7, 2),
    )
    assert selectors.total_value() == Decimal("502.00")
    assert ledger_selectors.monthly_income(month=date(2026, 7, 1)) == Decimal("0.00")

    services.record_income(
        wealth_account=wealth_account,
        income_category=category,
        amount=Decimal("3.00"),
        occurred_on=date(2026, 7, 3),
        daily_account=bank,
    )
    assert ledger_selectors.monthly_income(month=date(2026, 7, 1)) == Decimal("3.00")
    assert selectors.month_income(month=date(2026, 7, 1)) == Decimal("5.00")


@pytest.mark.django_db
def test_valuation_and_yuebao_sync(authenticated_client, wealth_account):
    services.update_valuation(
        wealth_account=wealth_account,
        current_value=Decimal("1234.56"),
        valuation_date=date(2026, 7, 3),
    )
    wealth_account.refresh_from_db()
    assert wealth_account.current_value == Decimal("1234.56")

    content = (
        "var Data_millionCopiesIncome = [[1767225600000,0.2234]];"
        "var Data_sevenDaysYearIncome = [[1767225600000,0.82]];"
    )
    with patch("apps.wealth.services.urlopen", return_value=context_bytes(content)):
        services.sync_yuebao(wealth_account=wealth_account)
    wealth_account.refresh_from_db()
    assert wealth_account.per_ten_thousand_income == Decimal("0.2234")
    assert wealth_account.seven_day_annual_yield == Decimal("0.82")


class context_bytes:
    def __init__(self, content):
        self.content = content.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.content
