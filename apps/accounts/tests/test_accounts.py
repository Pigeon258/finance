from decimal import Decimal
from importlib import import_module

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse

from apps.accounts import selectors, services
from apps.accounts.models import Account


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_superuser(
        username="owner", password="correct horse battery staple"
    )


@pytest.mark.django_db
def test_default_accounts_are_created_once():
    assert list(Account.objects.values_list("account_type", flat=True)) == [
        Account.AccountType.BANK,
        Account.AccountType.WECHAT,
        Account.AccountType.ALIPAY,
        Account.AccountType.CREDIT_CARD,
    ]

    migration = import_module("apps.accounts.migrations.0002_default_accounts")
    migration.create_default_accounts(apps, None)

    assert Account.objects.count() == 4


@pytest.mark.django_db
def test_credit_card_is_liability_and_other_defaults_are_assets():
    credit_card = Account.objects.get(account_type=Account.AccountType.CREDIT_CARD)
    assets = Account.objects.exclude(account_type=Account.AccountType.CREDIT_CARD)

    assert credit_card.balance_nature == Account.BalanceNature.LIABILITY
    assert set(assets.values_list("balance_nature", flat=True)) == {
        Account.BalanceNature.ASSET
    }


@pytest.mark.django_db
def test_create_account_derives_nature_and_uses_decimal():
    account = services.create_account(
        name="第二张银行卡",
        account_type=Account.AccountType.BANK,
        initial_balance=Decimal("123.45"),
        is_active=True,
        sort_order=50,
    )

    assert account.balance_nature == Account.BalanceNature.ASSET
    assert account.initial_balance == Decimal("123.45")


@pytest.mark.django_db
def test_negative_initial_balance_is_rejected_by_service():
    with pytest.raises(ValidationError):
        services.create_account(
            name="非法账户",
            account_type=Account.AccountType.BANK,
            initial_balance=Decimal("-0.01"),
            is_active=True,
            sort_order=50,
        )


@pytest.mark.django_db
def test_cash_account_type_is_not_supported():
    with pytest.raises(ValidationError):
        services.create_account(
            name="现金",
            account_type="CASH",
            initial_balance=Decimal("0.00"),
            is_active=True,
            sort_order=50,
        )


@pytest.mark.django_db
def test_invalid_account_nature_is_rejected_by_database():
    with pytest.raises(IntegrityError), transaction.atomic():
        Account.objects.create(
            name="非法账户",
            account_type=Account.AccountType.BANK,
            balance_nature=Account.BalanceNature.LIABILITY,
        )


@pytest.mark.django_db
def test_second_active_credit_card_is_rejected():
    with pytest.raises(ValidationError):
        services.create_account(
            name="第二张信用卡",
            account_type=Account.AccountType.CREDIT_CARD,
            initial_balance=Decimal("0.00"),
            is_active=True,
            sort_order=50,
        )


@pytest.mark.django_db
def test_selector_can_exclude_inactive_accounts():
    account = Account.objects.get(account_type=Account.AccountType.WECHAT)
    services.deactivate_account(account=account)

    assert account not in selectors.account_list(include_inactive=False)
    assert account in selectors.account_list(include_inactive=True)


@pytest.mark.django_db
def test_account_management_pages_require_login(client):
    assert client.get(reverse("accounts:index")).status_code == 302


@pytest.mark.django_db
def test_account_can_be_created_and_deactivated_through_pages(client, owner):
    client.force_login(owner)
    response = client.post(
        reverse("accounts:create"),
        {
            "name": "生活费银行卡",
            "account_type": Account.AccountType.BANK,
            "initial_balance": "100.00",
            "is_active": "on",
            "sort_order": 60,
            "opened_at": "",
            "note": "",
        },
    )
    account = Account.objects.get(name="生活费银行卡")

    assert response.status_code == 302
    assert account.initial_balance == Decimal("100.00")
    assert client.post(reverse("accounts:deactivate", args=[account.id])).status_code == 302
    account.refresh_from_db()
    assert account.is_active is False


@pytest.mark.django_db
def test_account_type_cannot_be_changed_by_edit_form(client, owner):
    client.force_login(owner)
    account = Account.objects.get(account_type=Account.AccountType.BANK)

    response = client.post(
        reverse("accounts:edit", args=[account.id]),
        {
            "name": account.name,
            "account_type": Account.AccountType.CREDIT_CARD,
            "initial_balance": "0.00",
            "is_active": "on",
            "sort_order": account.sort_order,
            "opened_at": "",
            "note": "",
        },
    )

    assert response.status_code == 302
    account.refresh_from_db()
    assert account.account_type == Account.AccountType.BANK
