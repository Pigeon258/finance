from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse

from apps.accounts.models import Account
from apps.ledger import selectors, services
from apps.ledger.models import Category, Tag, Transaction

PASSWORD = "correct horse battery staple"
OCCURRED_AT_VALUE = "2026-07-12T12:30"
OCCURRED_AT = datetime(2026, 7, 12, 12, 30, tzinfo=ZoneInfo("Asia/Shanghai"))


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_superuser(username="owner", password=PASSWORD)


@pytest.fixture
def authenticated_client(client, owner):
    client.force_login(owner)
    return client


@pytest.fixture
def accounts():
    return {
        account.account_type: account
        for account in Account.objects.filter(is_active=True).order_by("id")
    }


@pytest.fixture
def categories():
    return {
        "income": Category.objects.get(name="其他收入"),
        "expense": Category.objects.get(name="餐饮"),
    }


def _form_token(client, operation):
    response = client.get(reverse("ledger:transaction-create", args=[operation]))
    assert response.status_code == 200
    return response.context["submission_token"]


def _post_operation(client, operation, data):
    token = _form_token(client, operation)
    return client.post(
        reverse("ledger:transaction-create", args=[operation]),
        {**data, "submission_token": token},
    )


def _common_data(*, amount="10.00", channel=Transaction.Channel.BANK):
    return {
        "amount": amount,
        "occurred_at": OCCURRED_AT_VALUE,
        "channel": channel,
        "counterparty": "测试商家",
        "note": "页面录入",
    }


@pytest.mark.django_db
def test_transaction_pages_require_login(client):
    assert client.get(reverse("ledger:transaction-index")).status_code == 302
    assert (
        client.get(reverse("ledger:transaction-create", args=["income"])).status_code == 302
    )


@pytest.mark.django_db
def test_default_impulse_tag_is_available_for_expenses_only():
    tag = Tag.objects.get(name="冲动消费")
    assert tag.is_active is True
    assert tag.applies_to == Tag.AppliesTo.EXPENSE


@pytest.mark.django_db
def test_income_and_expense_forms_only_offer_matching_tags(authenticated_client):
    income_page = authenticated_client.get(
        reverse("ledger:transaction-create", args=["income"])
    ).content.decode()
    expense_page = authenticated_client.get(
        reverse("ledger:transaction-create", args=["expense"])
    ).content.decode()

    assert "冲动消费" not in income_page
    assert "冲动消费" in expense_page


@pytest.mark.django_db
def test_income_form_creates_manual_income(authenticated_client, accounts, categories):
    tag = Tag.objects.create(name="生活费", applies_to=Tag.AppliesTo.INCOME)
    response = _post_operation(
        authenticated_client,
        "income",
        {
            **_common_data(amount="1000.00"),
            "account": accounts[Account.AccountType.BANK].id,
            "category": categories["income"].id,
            "tags": [tag.id],
        },
    )

    ledger_transaction = Transaction.objects.get()
    assert response.status_code == 302
    assert response.url == reverse(
        "ledger:transaction-detail", args=[ledger_transaction.id]
    )
    assert ledger_transaction.source == Transaction.Source.MANUAL
    assert ledger_transaction.entries.get().balance_delta == Decimal("1000.00")
    assert list(ledger_transaction.tags.all()) == [tag]


@pytest.mark.django_db
def test_normal_expense_form_only_uses_asset_account(
    authenticated_client, accounts, categories
):
    response = _post_operation(
        authenticated_client,
        "expense",
        {
            **_common_data(channel=Transaction.Channel.WECHAT),
            "account": accounts[Account.AccountType.WECHAT].id,
            "category": categories["expense"].id,
        },
    )

    assert response.status_code == 302
    assert Transaction.objects.get().entries.get().balance_delta == Decimal("-10.00")


@pytest.mark.django_db
def test_credit_card_expense_form_increases_liability(
    authenticated_client, accounts, categories
):
    response = _post_operation(
        authenticated_client,
        "credit-card-expense",
        {
            **_common_data(channel=Transaction.Channel.OTHER),
            "account": accounts[Account.AccountType.CREDIT_CARD].id,
            "category": categories["expense"].id,
        },
    )

    assert response.status_code == 302
    assert Transaction.objects.get().entries.get().balance_delta == Decimal("10.00")


@pytest.mark.django_db
def test_transfer_form_keeps_channel_separate_from_accounts(
    authenticated_client, accounts
):
    response = _post_operation(
        authenticated_client,
        "transfer",
        {
            **_common_data(channel=Transaction.Channel.ALIPAY),
            "source_account": accounts[Account.AccountType.BANK].id,
            "destination_account": accounts[Account.AccountType.ALIPAY].id,
        },
    )

    ledger_transaction = Transaction.objects.get()
    assert response.status_code == 302
    assert ledger_transaction.channel == Transaction.Channel.ALIPAY
    assert ledger_transaction.entries.count() == 2
    assert selectors.monthly_net_expense(month=OCCURRED_AT.date()) == Decimal("0.00")


@pytest.mark.django_db
def test_repayment_form_creates_transfer_not_expense(authenticated_client, accounts):
    response = _post_operation(
        authenticated_client,
        "credit-card-repayment",
        {
            "amount": "20.00",
            "occurred_at": OCCURRED_AT_VALUE,
            "source_account": accounts[Account.AccountType.BANK].id,
            "credit_card_account": accounts[Account.AccountType.CREDIT_CARD].id,
            "channel": Transaction.Channel.BANK,
            "note": "全额还款",
        },
    )

    ledger_transaction = Transaction.objects.get()
    assert response.status_code == 302
    assert ledger_transaction.transaction_type == Transaction.TransactionType.TRANSFER
    assert ledger_transaction.entries.count() == 2
    assert selectors.monthly_net_expense(month=OCCURRED_AT.date()) == Decimal("0.00")


@pytest.mark.django_db
def test_balance_adjustment_form_does_not_count_as_income(authenticated_client, accounts):
    response = _post_operation(
        authenticated_client,
        "balance-adjustment",
        {
            "account": accounts[Account.AccountType.ALIPAY].id,
            "balance_delta": "-0.01",
            "occurred_at": OCCURRED_AT_VALUE,
            "reason": "余额核对",
        },
    )

    assert response.status_code == 302
    assert Transaction.objects.get().entries.get().balance_delta == Decimal("-0.01")
    assert selectors.monthly_income(month=OCCURRED_AT.date()) == Decimal("0.00")


@pytest.mark.django_db
def test_invalid_transfer_rolls_back_without_header(authenticated_client, accounts):
    account_id = accounts[Account.AccountType.BANK].id
    response = _post_operation(
        authenticated_client,
        "transfer",
        {
            **_common_data(),
            "source_account": account_id,
            "destination_account": account_id,
        },
    )

    assert response.status_code == 200
    assert "不能相同" in response.content.decode()
    assert Transaction.objects.count() == 0


@pytest.mark.django_db
def test_replayed_submission_token_does_not_duplicate_transaction(
    authenticated_client, accounts, categories
):
    token = _form_token(authenticated_client, "expense")
    url = reverse("ledger:transaction-create", args=["expense"])
    data = {
        **_common_data(),
        "account": accounts[Account.AccountType.BANK].id,
        "category": categories["expense"].id,
        "submission_token": token,
    }

    first = authenticated_client.post(url, data)
    second = authenticated_client.post(url, data)

    assert first.status_code == 302
    assert second.status_code == 302
    assert Transaction.objects.count() == 1


@pytest.mark.django_db
def test_unlocked_manual_expense_can_be_edited(authenticated_client, accounts, categories):
    expense = services.create_expense(
        account=accounts[Account.AccountType.BANK],
        category=categories["expense"],
        amount=Decimal("10.00"),
        occurred_at=OCCURRED_AT,
        channel=Transaction.Channel.BANK,
    )
    edit_url = reverse("ledger:transaction-edit", args=[expense.id])
    get_response = authenticated_client.get(edit_url)
    token = get_response.context["submission_token"]

    response = authenticated_client.post(
        edit_url,
        {
            **_common_data(amount="25.00"),
            "account": accounts[Account.AccountType.WECHAT].id,
            "category": categories["expense"].id,
            "submission_token": token,
        },
    )

    expense.refresh_from_db()
    assert response.status_code == 302
    assert expense.amount == Decimal("25.00")
    assert expense.entries.get().account == accounts[Account.AccountType.WECHAT]
    assert expense.entries.get().balance_delta == Decimal("-25.00")


@pytest.mark.django_db
def test_locked_transaction_edit_url_redirects_to_detail(
    authenticated_client, accounts, categories
):
    expense = services.create_expense(
        account=accounts[Account.AccountType.BANK],
        category=categories["expense"],
        amount=Decimal("10.00"),
        occurred_at=OCCURRED_AT,
        channel=Transaction.Channel.BANK,
    )
    services.lock_transaction(ledger_transaction=expense)

    response = authenticated_client.get(reverse("ledger:transaction-edit", args=[expense.id]))

    assert response.status_code == 302
    assert response.url == reverse("ledger:transaction-detail", args=[expense.id])


@pytest.mark.django_db
def test_unlocked_transaction_can_be_voided(authenticated_client, accounts, categories):
    expense = services.create_expense(
        account=accounts[Account.AccountType.BANK],
        category=categories["expense"],
        amount=Decimal("10.00"),
        occurred_at=OCCURRED_AT,
        channel=Transaction.Channel.BANK,
    )

    response = authenticated_client.post(
        reverse("ledger:transaction-void", args=[expense.id]), {"reason": "重复记录"}
    )

    expense.refresh_from_db()
    assert response.status_code == 302
    assert expense.status == Transaction.Status.VOID


@pytest.mark.django_db
def test_transaction_filters_combine_and_escape_keyword(
    authenticated_client, accounts, categories
):
    matching = services.create_expense(
        account=accounts[Account.AccountType.BANK],
        category=categories["expense"],
        amount=Decimal("12.00"),
        occurred_at=OCCURRED_AT,
        channel=Transaction.Channel.BANK,
        counterparty="商家%_特殊",
    )
    services.create_income(
        account=accounts[Account.AccountType.BANK],
        category=categories["income"],
        amount=Decimal("99.00"),
        occurred_at=OCCURRED_AT,
        channel=Transaction.Channel.BANK,
        counterparty="其他",
    )

    response = authenticated_client.get(
        reverse("ledger:transaction-index"),
        {
            "date_from": "2026-07-01",
            "date_to": "2026-07-31",
            "transaction_type": Transaction.TransactionType.EXPENSE,
            "account": accounts[Account.AccountType.BANK].id,
            "category": categories["expense"].id,
            "amount_min": "10.00",
            "amount_max": "20.00",
            "keyword": "%_",
        },
    )

    assert response.status_code == 200
    assert list(response.context["page"].object_list) == [matching]


@pytest.mark.django_db
def test_transaction_pagination_is_stable(authenticated_client, accounts, categories):
    for index in range(26):
        services.create_expense(
            account=accounts[Account.AccountType.BANK],
            category=categories["expense"],
            amount=Decimal("1.00"),
            occurred_at=OCCURRED_AT + timedelta(minutes=index),
            channel=Transaction.Channel.BANK,
        )

    first_page = authenticated_client.get(reverse("ledger:transaction-index"))
    second_page = authenticated_client.get(
        reverse("ledger:transaction-index"), {"page": 2}
    )

    first_ids = [item.id for item in first_page.context["page"]]
    second_ids = [item.id for item in second_page.context["page"]]
    assert len(first_ids) == 25
    assert len(second_ids) == 1
    assert set(first_ids).isdisjoint(second_ids)
