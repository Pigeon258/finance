from datetime import datetime
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.urls import reverse

from apps.accounts.models import Account, AccountReconciliation
from apps.ledger import selectors, services
from apps.ledger.models import Category, Transaction

OCCURRED_AT = datetime(2026, 7, 12, 12, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
FORM_OCCURRED_AT = "2026-07-12T12:30"


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
def bank():
    return Account.objects.get(account_type=Account.AccountType.BANK)


@pytest.fixture
def credit_card():
    return Account.objects.get(account_type=Account.AccountType.CREDIT_CARD)


@pytest.fixture
def expense_category():
    return Category.objects.get(name="餐饮")


def _expense(*, bank, expense_category, amount=Decimal("100.00")):
    return services.create_expense(
        account=bank,
        category=expense_category,
        amount=amount,
        occurred_at=OCCURRED_AT,
        channel=Transaction.Channel.BANK,
        counterparty="原商家",
    )


@pytest.mark.django_db
def test_reconciliation_snapshot_and_adjustment_are_atomic(bank, expense_category):
    bank.initial_balance = Decimal("100.00")
    bank.save(update_fields=["initial_balance"])
    _expense(bank=bank, expense_category=expense_category, amount=Decimal("10.00"))

    reconciliation = services.reconcile_account(
        account=bank,
        actual_balance=Decimal("80.00"),
        checked_at=OCCURRED_AT,
        note="实际少十元",
        create_adjustment=True,
    )

    assert reconciliation.calculated_balance == Decimal("90.00")
    assert reconciliation.difference == Decimal("-10.00")
    assert reconciliation.adjustment_transaction_id is not None
    adjustment = Transaction.objects.get(pk=reconciliation.adjustment_transaction_id)
    assert adjustment.transaction_type == Transaction.TransactionType.BALANCE_ADJUSTMENT
    assert adjustment.entries.get().balance_delta == Decimal("-10.00")
    assert selectors.account_balance(account=bank) == Decimal("80.00")
    assert selectors.monthly_net_expense(month=OCCURRED_AT.date()) == Decimal("10.00")


@pytest.mark.django_db
def test_reconciliation_without_adjustment_preserves_balance(bank):
    before = selectors.account_balance(account=bank)
    reconciliation = services.reconcile_account(
        account=bank,
        actual_balance=Decimal("12.34"),
        checked_at=OCCURRED_AT,
        create_adjustment=False,
    )

    assert reconciliation.difference == Decimal("12.34")
    assert reconciliation.adjustment_transaction_id is None
    assert selectors.account_balance(account=bank) == before


@pytest.mark.django_db
def test_zero_difference_does_not_create_redundant_adjustment(bank):
    reconciliation = services.reconcile_account(
        account=bank,
        actual_balance=Decimal("0.00"),
        checked_at=OCCURRED_AT,
        create_adjustment=True,
    )

    assert reconciliation.difference == Decimal("0.00")
    assert reconciliation.adjustment_transaction_id is None
    assert Transaction.objects.count() == 0


@pytest.mark.django_db
def test_credit_card_refund_reduces_liability(credit_card, expense_category):
    expense = services.create_credit_card_purchase(
        account=credit_card,
        category=expense_category,
        amount=Decimal("100.00"),
        occurred_at=OCCURRED_AT,
        channel=Transaction.Channel.OTHER,
    )

    refund = services.create_refund(
        original_transaction=expense,
        amount=Decimal("30.00"),
        occurred_at=OCCURRED_AT,
    )

    assert refund.entries.get().balance_delta == Decimal("-30.00")
    assert selectors.current_liabilities() == Decimal("70.00")
    assert selectors.monthly_net_expense(month=OCCURRED_AT.date()) == Decimal("70.00")


@pytest.mark.django_db
def test_refund_relation_protects_original_from_physical_deletion(bank, expense_category):
    expense = _expense(bank=bank, expense_category=expense_category)
    services.create_refund(
        original_transaction=expense,
        amount=Decimal("10.00"),
        occurred_at=OCCURRED_AT,
    )

    with pytest.raises(ProtectedError):
        expense.delete()


@pytest.mark.django_db
def test_locked_expense_correction_creates_reversal_and_replacement(bank, expense_category):
    expense = _expense(bank=bank, expense_category=expense_category, amount=Decimal("10.00"))
    services.lock_transaction(ledger_transaction=expense)

    replacement, reversals = services.correct_expense(
        ledger_transaction=expense,
        correction_occurred_at=OCCURRED_AT,
        reason="金额录错",
        account=bank,
        category=expense_category,
        amount=Decimal("25.00"),
        occurred_at=OCCURRED_AT,
        channel=Transaction.Channel.BANK,
        counterparty="正确商家",
    )

    expense.refresh_from_db()
    assert expense.status == Transaction.Status.REVERSED
    assert len(reversals) == 1
    assert reversals[0].entries.get().balance_delta == Decimal("10.00")
    assert replacement.related_transaction == expense
    assert replacement.is_financial_locked is True
    assert selectors.account_balance(account=bank) == Decimal("-25.00")
    assert selectors.monthly_net_expense(month=OCCURRED_AT.date()) == Decimal("25.00")


@pytest.mark.django_db
def test_correction_failure_rolls_back_reversal(bank, expense_category):
    expense = _expense(bank=bank, expense_category=expense_category)
    services.lock_transaction(ledger_transaction=expense)
    transaction_count = Transaction.objects.count()

    with (
        patch("apps.ledger.services.create_expense", side_effect=ValidationError("失败")),
        pytest.raises(ValidationError, match="失败"),
    ):
        services.correct_expense(
            ledger_transaction=expense,
            correction_occurred_at=OCCURRED_AT,
            reason="测试回滚",
            account=bank,
            category=expense_category,
            amount=Decimal("20.00"),
            occurred_at=OCCURRED_AT,
            channel=Transaction.Channel.BANK,
        )

    expense.refresh_from_db()
    assert expense.status == Transaction.Status.ACTIVE
    assert Transaction.objects.count() == transaction_count
    assert selectors.account_balance(account=bank) == Decimal("-100.00")


@pytest.mark.django_db
def test_unlocked_transaction_must_not_use_correction_flow(bank, expense_category):
    expense = _expense(bank=bank, expense_category=expense_category)

    with pytest.raises(ValidationError, match="直接编辑"):
        services.correct_expense(
            ledger_transaction=expense,
            correction_occurred_at=OCCURRED_AT,
            reason="不应修正",
            account=bank,
            category=expense_category,
            amount=Decimal("20.00"),
            occurred_at=OCCURRED_AT,
            channel=Transaction.Channel.BANK,
        )


@pytest.mark.django_db
def test_refund_page_records_partial_refund(authenticated_client, bank, expense_category):
    expense = _expense(bank=bank, expense_category=expense_category)
    url = reverse("ledger:transaction-refund", args=[expense.id])
    get_response = authenticated_client.get(url)
    token = get_response.context["submission_token"]

    response = authenticated_client.post(
        url,
        {
            "amount": "40.00",
            "occurred_at": FORM_OCCURRED_AT,
            "account": bank.id,
            "channel": Transaction.Channel.BANK,
            "note": "部分退款",
            "submission_token": token,
        },
    )

    refund = Transaction.objects.get(transaction_type=Transaction.TransactionType.REFUND)
    assert response.status_code == 302
    assert refund.related_transaction == expense
    assert refund.category == expense.category
    assert refund.budget_month == expense.budget_month
    assert selectors.refundable_remaining(original_transaction=expense) == Decimal("60.00")


@pytest.mark.django_db
def test_reconciliation_page_creates_snapshot_and_adjustment(authenticated_client, bank):
    url = reverse("ledger:account-reconcile", args=[bank.id])
    token = authenticated_client.get(url).context["submission_token"]

    response = authenticated_client.post(
        url,
        {
            "actual_balance": "50.00",
            "checked_at": FORM_OCCURRED_AT,
            "note": "首次核对",
            "create_adjustment": "on",
            "submission_token": token,
        },
    )

    reconciliation = AccountReconciliation.objects.get()
    assert response.status_code == 302
    assert reconciliation.difference == Decimal("50.00")
    assert reconciliation.adjustment_transaction_id is not None
    assert selectors.account_balance(account=bank) == Decimal("50.00")


@pytest.mark.django_db
def test_correction_page_replaces_locked_transaction(
    authenticated_client, bank, expense_category
):
    expense = _expense(bank=bank, expense_category=expense_category)
    services.lock_transaction(ledger_transaction=expense)
    url = reverse("ledger:transaction-correct", args=[expense.id])
    token = authenticated_client.get(url).context["submission_token"]

    response = authenticated_client.post(
        url,
        {
            "correction_reason": "金额错误",
            "amount": "80.00",
            "occurred_at": FORM_OCCURRED_AT,
            "channel": Transaction.Channel.BANK,
            "counterparty": "正确商家",
            "note": "替代记录",
            "account": bank.id,
            "category": expense_category.id,
            "submission_token": token,
        },
    )

    expense.refresh_from_db()
    replacement = Transaction.objects.get(
        transaction_type=Transaction.TransactionType.EXPENSE,
        status=Transaction.Status.ACTIVE,
    )
    assert response.status_code == 302
    assert response.url == reverse("ledger:transaction-detail", args=[replacement.id])
    assert expense.status == Transaction.Status.REVERSED
    assert replacement.amount == Decimal("80.00")
