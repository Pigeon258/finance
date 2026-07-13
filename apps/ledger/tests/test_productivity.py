from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse

from apps.accounts.models import Account
from apps.ledger import services
from apps.ledger.models import Category, Transaction, TransactionTemplate

NOW = datetime(2026, 7, 13, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_superuser(
        username="owner", password="correct horse battery staple"
    )


@pytest.fixture
def authenticated_client(client, owner):
    client.force_login(owner)
    return client


@pytest.fixture
def accounts():
    return {account.account_type: account for account in Account.objects.filter(is_active=True)}


@pytest.fixture
def expense_category():
    return Category.objects.get(name="餐饮")


def _expense(*, account, category, occurred_at=NOW, counterparty="示例商家"):
    return services.create_expense(
        account=account,
        category=category,
        amount=Decimal("12.34"),
        occurred_at=occurred_at,
        channel=Transaction.Channel.WECHAT,
        counterparty=counterparty,
        note="原备注",
    )


@pytest.mark.django_db
def test_copy_prefills_new_manual_fact_without_relations_or_lock(
    authenticated_client, accounts, expense_category
):
    original = _expense(account=accounts[Account.AccountType.WECHAT], category=expense_category)
    refund = services.create_refund(
        original_transaction=original,
        amount=Decimal("2.00"),
        occurred_at=NOW + timedelta(hours=1),
    )
    response = authenticated_client.get(reverse("ledger:transaction-copy", args=[original.id]))
    assert response.status_code == 302
    form_page = authenticated_client.get(response.url)
    assert form_page.context["form"].initial["amount"] == Decimal("12.34")
    token = form_page.context["submission_token"]
    create_response = authenticated_client.post(
        response.url,
        {
            "amount": "12.34",
            "occurred_at": "2026-07-13T12:00",
            "channel": Transaction.Channel.WECHAT,
            "counterparty": "示例商家",
            "note": "原备注",
            "account": accounts[Account.AccountType.WECHAT].id,
            "category": expense_category.id,
            "submission_token": token,
        },
    )
    copied = Transaction.objects.exclude(id__in=[original.id, refund.id]).get()
    assert create_response.status_code == 302
    assert copied.id != original.id
    assert copied.amount == Decimal("12.34")
    assert copied.source == Transaction.Source.MANUAL
    assert copied.is_financial_locked is False
    assert copied.related_transaction_id is None
    assert copied.related_transactions.count() == 0


@pytest.mark.django_db
def test_refund_cannot_be_copied(authenticated_client, accounts, expense_category):
    original = _expense(account=accounts[Account.AccountType.BANK], category=expense_category)
    refund = services.create_refund(
        original_transaction=original,
        amount=Decimal("1.00"),
        occurred_at=NOW + timedelta(hours=1),
    )
    response = authenticated_client.get(reverse("ledger:transaction-copy", args=[refund.id]))
    assert response.status_code == 302
    assert response.url == reverse("ledger:transaction-detail", args=[refund.id])
    assert Transaction.objects.count() == 2


@pytest.mark.django_db
def test_template_does_not_create_transaction_and_only_prefills_form(
    authenticated_client, accounts, expense_category
):
    response = authenticated_client.post(
        reverse("ledger:transaction-template-create"),
        {
            "name": "午餐",
            "operation": TransactionTemplate.Operation.EXPENSE,
            "amount": "18.50",
            "primary_account": accounts[Account.AccountType.WECHAT].id,
            "secondary_account": "",
            "category": expense_category.id,
            "channel": Transaction.Channel.WECHAT,
            "counterparty": "食堂",
            "note": "工作日午餐",
            "is_active": "on",
            "sort_order": 10,
        },
    )
    template = TransactionTemplate.objects.get()
    assert response.status_code == 302
    assert template.amount == Decimal("18.50")
    assert Transaction.objects.count() == 0

    form_page = authenticated_client.get(
        reverse("ledger:transaction-create", args=["expense"]), {"template": template.id}
    )
    assert form_page.context["form"].initial["amount"] == Decimal("18.50")
    assert form_page.context["form"].initial["account"] == accounts[Account.AccountType.WECHAT]
    assert Transaction.objects.count() == 0


@pytest.mark.django_db
def test_recent_account_becomes_default_without_duplicate_storage(
    authenticated_client, accounts, expense_category
):
    _expense(
        account=accounts[Account.AccountType.BANK],
        category=expense_category,
        occurred_at=NOW - timedelta(days=1),
    )
    _expense(
        account=accounts[Account.AccountType.WECHAT],
        category=expense_category,
        occurred_at=NOW,
    )
    response = authenticated_client.get(reverse("ledger:transaction-create", args=["expense"]))
    assert response.context["form"].initial["account"] == accounts[Account.AccountType.WECHAT]
