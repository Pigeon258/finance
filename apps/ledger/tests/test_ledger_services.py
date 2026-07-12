from datetime import datetime
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db import transaction as db_transaction
from django.db.models.deletion import ProtectedError

from apps.accounts.models import Account
from apps.ledger import selectors, services
from apps.ledger.models import Category, Merchant, Tag, Transaction, TransactionEntry

OCCURRED_AT = datetime(2026, 7, 12, 12, 30, tzinfo=ZoneInfo("Asia/Shanghai"))


@pytest.fixture
def bank():
    return Account.objects.get(account_type=Account.AccountType.BANK)


@pytest.fixture
def wechat():
    return Account.objects.get(account_type=Account.AccountType.WECHAT)


@pytest.fixture
def credit_card():
    return Account.objects.get(account_type=Account.AccountType.CREDIT_CARD)


@pytest.fixture
def income_category():
    return Category.objects.get(name="其他收入")


@pytest.fixture
def expense_category():
    return Category.objects.get(name="餐饮")


@pytest.mark.django_db
def test_income_creates_one_positive_asset_entry(bank, income_category):
    ledger_transaction = services.create_income(
        account=bank,
        category=income_category,
        amount=Decimal("1000.00"),
        occurred_at=OCCURRED_AT,
        channel=Transaction.Channel.BANK,
    )

    entry = ledger_transaction.entries.get()
    assert ledger_transaction.transaction_type == Transaction.TransactionType.INCOME
    assert ledger_transaction.budget_month.isoformat() == "2026-07-01"
    assert entry.account == bank
    assert entry.balance_delta == Decimal("1000.00")
    assert selectors.account_balance(account=bank) == Decimal("1000.00")


@pytest.mark.django_db
def test_asset_expense_decreases_asset(bank, expense_category):
    ledger_transaction = services.create_expense(
        account=bank,
        category=expense_category,
        amount=Decimal("50.00"),
        occurred_at=OCCURRED_AT,
        channel=Transaction.Channel.BANK,
    )

    assert ledger_transaction.entries.get().balance_delta == Decimal("-50.00")
    assert selectors.account_balance(account=bank) == Decimal("-50.00")


@pytest.mark.django_db
def test_credit_card_purchase_increases_liability(credit_card, expense_category):
    ledger_transaction = services.create_credit_card_purchase(
        account=credit_card,
        category=expense_category,
        amount=Decimal("200.00"),
        occurred_at=OCCURRED_AT,
        channel=Transaction.Channel.OTHER,
    )

    assert ledger_transaction.entries.get().balance_delta == Decimal("200.00")
    assert selectors.current_liabilities() == Decimal("200.00")


@pytest.mark.django_db
def test_asset_transfer_preserves_total_liquid_assets(bank, wechat):
    bank.initial_balance = Decimal("1000.00")
    bank.save(update_fields=["initial_balance"])
    before = selectors.liquid_assets()

    ledger_transaction = services.create_transfer(
        source_account=bank,
        destination_account=wechat,
        amount=Decimal("500.00"),
        occurred_at=OCCURRED_AT,
    )

    assert list(
        ledger_transaction.entries.order_by("balance_delta").values_list(
            "balance_delta", flat=True
        )
    ) == [Decimal("-500.00"), Decimal("500.00")]
    assert selectors.liquid_assets() == before
    assert selectors.monthly_income(month=OCCURRED_AT.date()) == Decimal("0.00")
    assert selectors.monthly_net_expense(month=OCCURRED_AT.date()) == Decimal("0.00")


@pytest.mark.django_db
def test_credit_card_repayment_reduces_asset_and_liability_without_expense(
    bank, credit_card, expense_category
):
    bank.initial_balance = Decimal("500.00")
    bank.save(update_fields=["initial_balance"])
    services.create_credit_card_purchase(
        account=credit_card,
        category=expense_category,
        amount=Decimal("300.00"),
        occurred_at=OCCURRED_AT,
        channel=Transaction.Channel.OTHER,
    )

    repayment = services.create_credit_card_repayment(
        source_account=bank,
        credit_card_account=credit_card,
        amount=Decimal("300.00"),
        occurred_at=OCCURRED_AT,
    )

    assert set(repayment.entries.values_list("balance_delta", flat=True)) == {
        Decimal("-300.00")
    }
    assert selectors.account_balance(account=bank) == Decimal("200.00")
    assert selectors.current_liabilities() == Decimal("0.00")
    assert selectors.monthly_net_expense(month=OCCURRED_AT.date()) == Decimal("300.00")


@pytest.mark.django_db
def test_partial_refunds_reverse_original_account_and_budget(bank, expense_category):
    expense = services.create_expense(
        account=bank,
        category=expense_category,
        amount=Decimal("100.00"),
        occurred_at=OCCURRED_AT,
        channel=Transaction.Channel.BANK,
    )
    first = services.create_refund(
        original_transaction=expense,
        amount=Decimal("40.00"),
        occurred_at=OCCURRED_AT,
    )
    second = services.create_refund(
        original_transaction=expense,
        amount=Decimal("60.00"),
        occurred_at=OCCURRED_AT,
    )

    assert first.entries.get().balance_delta == Decimal("40.00")
    assert second.budget_month == expense.budget_month
    assert second.category == expense.category
    assert selectors.account_balance(account=bank) == Decimal("0.00")
    assert selectors.monthly_net_expense(month=OCCURRED_AT.date()) == Decimal("0.00")
    expense.refresh_from_db()
    assert expense.is_financial_locked is True


@pytest.mark.django_db
def test_refund_total_cannot_exceed_original(bank, expense_category):
    expense = services.create_expense(
        account=bank,
        category=expense_category,
        amount=Decimal("100.00"),
        occurred_at=OCCURRED_AT,
        channel=Transaction.Channel.BANK,
    )
    services.create_refund(
        original_transaction=expense,
        amount=Decimal("90.00"),
        occurred_at=OCCURRED_AT,
    )

    with pytest.raises(ValidationError, match="不得超过"):
        services.create_refund(
            original_transaction=expense,
            amount=Decimal("10.01"),
            occurred_at=OCCURRED_AT,
        )

    assert (
        Transaction.objects.filter(
            transaction_type=Transaction.TransactionType.REFUND
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_balance_adjustment_changes_balance_but_not_income_or_expense(bank):
    adjustment = services.create_balance_adjustment(
        account=bank,
        balance_delta=Decimal("12.34"),
        occurred_at=OCCURRED_AT,
        reason="余额核对",
    )

    assert adjustment.amount == Decimal("12.34")
    assert selectors.account_balance(account=bank) == Decimal("12.34")
    assert selectors.monthly_income(month=OCCURRED_AT.date()) == Decimal("0.00")
    assert selectors.monthly_net_expense(month=OCCURRED_AT.date()) == Decimal("0.00")


@pytest.mark.django_db
def test_void_transaction_no_longer_affects_balance(bank, expense_category):
    expense = services.create_expense(
        account=bank,
        category=expense_category,
        amount=Decimal("0.01"),
        occurred_at=OCCURRED_AT,
        channel=Transaction.Channel.BANK,
    )
    assert selectors.account_balance(account=bank) == Decimal("-0.01")

    services.void_transaction(ledger_transaction=expense, reason="重复录入")

    assert selectors.account_balance(account=bank) == Decimal("0.00")
    assert selectors.monthly_net_expense(month=OCCURRED_AT.date()) == Decimal("0.00")


@pytest.mark.django_db
def test_locked_transaction_cannot_be_voided(bank, expense_category):
    expense = services.create_expense(
        account=bank,
        category=expense_category,
        amount=Decimal("1.00"),
        occurred_at=OCCURRED_AT,
        channel=Transaction.Channel.BANK,
    )
    services.lock_transaction(ledger_transaction=expense)

    with pytest.raises(ValidationError, match="不能直接作废"):
        services.void_transaction(ledger_transaction=expense, reason="错误")


@pytest.mark.django_db
def test_reverse_transaction_creates_system_adjustment(bank, expense_category):
    expense = services.create_expense(
        account=bank,
        category=expense_category,
        amount=Decimal("25.00"),
        occurred_at=OCCURRED_AT,
        channel=Transaction.Channel.BANK,
    )

    reversals = services.reverse_transaction(
        ledger_transaction=expense, occurred_at=OCCURRED_AT, reason="核心金额错误"
    )

    expense.refresh_from_db()
    assert expense.status == Transaction.Status.REVERSED
    assert len(reversals) == 1
    assert reversals[0].source == Transaction.Source.SYSTEM
    assert reversals[0].entries.get().balance_delta == Decimal("25.00")
    assert selectors.account_balance(account=bank) == Decimal("0.00")
    assert selectors.monthly_net_expense(month=OCCURRED_AT.date()) == Decimal("0.00")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "invalid_amount",
    [Decimal("0.00"), Decimal("-1.00"), Decimal("0.001"), Decimal("1000000000000.00"), 1.0],
)
def test_invalid_expense_amounts_are_rejected(bank, expense_category, invalid_amount):
    with pytest.raises(ValidationError):
        services.create_expense(
            account=bank,
            category=expense_category,
            amount=invalid_amount,
            occurred_at=OCCURRED_AT,
            channel=Transaction.Channel.BANK,
        )


@pytest.mark.django_db
def test_invalid_category_and_account_combinations_are_rejected(
    bank, credit_card, income_category, expense_category
):
    with pytest.raises(ValidationError, match="分类类型"):
        services.create_expense(
            account=bank,
            category=income_category,
            amount=Decimal("1.00"),
            occurred_at=OCCURRED_AT,
            channel=Transaction.Channel.BANK,
        )
    with pytest.raises(ValidationError, match="负债账户"):
        services.create_credit_card_purchase(
            account=bank,
            category=expense_category,
            amount=Decimal("1.00"),
            occurred_at=OCCURRED_AT,
            channel=Transaction.Channel.BANK,
        )
    with pytest.raises(ValidationError, match="账户性质"):
        services.create_income(
            account=credit_card,
            category=income_category,
            amount=Decimal("1.00"),
            occurred_at=OCCURRED_AT,
            channel=Transaction.Channel.BANK,
        )


@pytest.mark.django_db
def test_service_rolls_back_header_when_later_validation_fails(bank, income_category):
    inactive_tag = Tag.objects.create(name="停用标签", is_active=False)
    before = Transaction.objects.count()

    with pytest.raises(ValidationError, match="停用标签"):
        services.create_income(
            account=bank,
            category=income_category,
            amount=Decimal("10.00"),
            occurred_at=OCCURRED_AT,
            channel=Transaction.Channel.BANK,
            tags=[inactive_tag],
        )

    assert Transaction.objects.count() == before


@pytest.mark.django_db
def test_merchant_and_tags_are_attached_without_changing_entries(bank, expense_category):
    merchant = Merchant.objects.create(name="测试商家", normalized_name="测试商家")
    tag = Tag.objects.create(name="冲动消费")
    expense = services.create_expense(
        account=bank,
        category=expense_category,
        amount=Decimal("20.00"),
        occurred_at=OCCURRED_AT,
        channel=Transaction.Channel.ALIPAY,
        merchant=merchant,
        tags=[tag],
    )

    assert list(expense.tags.all()) == [tag]
    assert expense.merchant == merchant
    assert expense.entries.count() == 1


@pytest.mark.django_db
def test_database_rejects_zero_entry_delta(bank, income_category):
    ledger_transaction = services.create_income(
        account=bank,
        category=income_category,
        amount=Decimal("1.00"),
        occurred_at=OCCURRED_AT,
        channel=Transaction.Channel.BANK,
    )
    with pytest.raises(IntegrityError), db_transaction.atomic():
        TransactionEntry.objects.create(
            transaction=ledger_transaction,
            account=Account.objects.get(account_type=Account.AccountType.WECHAT),
            balance_delta=Decimal("0.00"),
        )


@pytest.mark.django_db
def test_entry_failure_rolls_back_entire_transaction(bank, income_category):
    before = Transaction.objects.count()
    with (
        patch("apps.ledger.services._create_entry", side_effect=ValidationError("entry failed")),
        pytest.raises(ValidationError, match="entry failed"),
    ):
        services.create_income(
            account=bank,
            category=income_category,
            amount=Decimal("10.00"),
            occurred_at=OCCURRED_AT,
            channel=Transaction.Channel.BANK,
        )
    assert Transaction.objects.count() == before


@pytest.mark.django_db
def test_accounts_and_categories_with_history_are_protected(bank, expense_category):
    services.create_expense(
        account=bank,
        category=expense_category,
        amount=Decimal("1.00"),
        occurred_at=OCCURRED_AT,
        channel=Transaction.Channel.BANK,
    )

    with pytest.raises(ProtectedError):
        bank.delete()
    with pytest.raises(ProtectedError):
        expense_category.delete()
