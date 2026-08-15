from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse

from apps.accounts.models import Account
from apps.credit import selectors, services
from apps.credit.models import BillingCycle, BillingCycleItem, CreditCardProfile
from apps.ledger import services as ledger_services
from apps.ledger.models import Category, Transaction

TZ = ZoneInfo("Asia/Shanghai")


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
    account = Account.objects.get(account_type=Account.AccountType.BANK)
    account.initial_balance = Decimal("2000.00")
    account.save(update_fields=["initial_balance"])
    return account


@pytest.fixture
def card():
    return Account.objects.get(account_type=Account.AccountType.CREDIT_CARD)


@pytest.fixture
def expense_category():
    return Category.objects.get(name="餐饮")


@pytest.fixture
def profile(card):
    return services.save_profile(
        account=card,
        credit_limit=Decimal("10000.00"),
        personal_monthly_limit=Decimal("2000.00"),
        statement_day=15,
        due_day=5,
    )


def _occurred(year, month, day):
    return datetime(year, month, day, 12, 0, tzinfo=TZ)


def _purchase(*, profile, expense_category, amount, occurred_at):
    return services.create_credit_card_purchase(
        profile=profile,
        account=profile.account,
        category=expense_category,
        amount=amount,
        occurred_at=occurred_at,
        channel=Transaction.Channel.OTHER,
    )


def _issue(cycle, amount):
    return services.issue_cycle(
        cycle=cycle,
        official_statement_amount=amount,
        official_due_amount=amount,
        due_date=cycle.due_date,
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("occurred_on", "statement_day", "due_day", "expected"),
    [
        (date(2024, 2, 15), 31, 31, (date(2024, 2, 1), date(2024, 2, 29), date(2024, 3, 31))),
        (date(2023, 2, 28), 31, 31, (date(2023, 2, 1), date(2023, 2, 28), date(2023, 3, 31))),
        (date(2026, 12, 20), 15, 5, (date(2026, 12, 16), date(2027, 1, 15), date(2027, 2, 5))),
        (date(2026, 3, 1), 31, 5, (date(2026, 3, 1), date(2026, 3, 31), date(2026, 4, 5))),
    ],
)
def test_cycle_date_boundaries(card, occurred_on, statement_day, due_day, expected):
    profile = CreditCardProfile(account=card, statement_day=statement_day, due_day=due_day)
    assert services.cycle_dates_for(profile=profile, occurred_on=occurred_on) == expected


@pytest.mark.django_db
def test_profile_must_use_liability_and_only_one_active_profile(card):
    bank = Account.objects.get(account_type=Account.AccountType.BANK)
    with pytest.raises(ValidationError):
        services.save_profile(
            account=bank,
            credit_limit=Decimal("1.00"),
            personal_monthly_limit=Decimal("1.00"),
            statement_day=1,
            due_day=1,
        )
    services.save_profile(
        account=card,
        credit_limit=Decimal("1.00"),
        personal_monthly_limit=Decimal("1.00"),
        statement_day=1,
        due_day=1,
    )
    with pytest.raises((ValidationError, IntegrityError)), transaction.atomic():
        CreditCardProfile.objects.create(
            account=card,
            credit_limit=Decimal("1.00"),
            personal_monthly_limit=Decimal("1.00"),
            statement_day=2,
            due_day=2,
            is_active=True,
        )


@pytest.mark.django_db
def test_purchase_is_atomically_assigned_to_open_cycle(profile, expense_category):
    purchase = _purchase(
        profile=profile,
        expense_category=expense_category,
        amount=Decimal("123.45"),
        occurred_at=_occurred(2026, 7, 10),
    )

    cycle = BillingCycle.objects.get()
    item = cycle.items.get()
    assert cycle.cycle_start == date(2026, 6, 16)
    assert cycle.cycle_end == date(2026, 7, 15)
    assert cycle.due_date == date(2026, 8, 5)
    assert item.transaction == purchase
    assert item.item_type == BillingCycleItem.ItemType.CHARGE
    assert item.allocated_amount == Decimal("123.45")
    assert selectors.current_liability(profile=profile) == Decimal("123.45")


@pytest.mark.django_db
def test_issue_uses_official_due_and_locks_transactions(profile, expense_category):
    purchase = _purchase(
        profile=profile,
        expense_category=expense_category,
        amount=Decimal("100.00"),
        occurred_at=_occurred(2026, 7, 10),
    )
    cycle = BillingCycle.objects.get()

    original_refresh = services._refresh_cycle_status
    with patch(
        "apps.credit.services._refresh_cycle_status",
        side_effect=lambda *, cycle, as_of=None: original_refresh(
            cycle=cycle, as_of=as_of or date(2026, 7, 20)
        ),
    ):
        services.issue_cycle(
            cycle=cycle,
            official_statement_amount=Decimal("101.00"),
            official_due_amount=Decimal("99.00"),
            due_date=date(2026, 8, 5),
            note="以银行账单为准",
        )

    cycle.refresh_from_db()
    purchase.refresh_from_db()
    assert selectors.cycle_calculated_statement_amount(cycle=cycle) == Decimal("100.00")
    assert selectors.cycle_due_base(cycle=cycle) == Decimal("99.00")
    assert selectors.cycle_remaining_due(cycle=cycle) == Decimal("99.00")
    assert cycle.status == BillingCycle.Status.ISSUED
    assert purchase.is_financial_locked is True


@pytest.mark.django_db
def test_partial_full_cross_cycle_and_excess_repayment(profile, bank, expense_category):
    _purchase(
        profile=profile,
        expense_category=expense_category,
        amount=Decimal("100.00"),
        occurred_at=_occurred(2026, 7, 10),
    )
    first_cycle = BillingCycle.objects.get()
    _issue(first_cycle, Decimal("100.00"))
    _purchase(
        profile=profile,
        expense_category=expense_category,
        amount=Decimal("100.00"),
        occurred_at=_occurred(2026, 8, 10),
    )
    second_cycle = BillingCycle.objects.get(status=BillingCycle.Status.OPEN)
    _issue(second_cycle, Decimal("100.00"))

    first_repayment = services.create_credit_card_repayment(
        profile=profile,
        source_account=bank,
        credit_card_account=profile.account,
        amount=Decimal("150.00"),
        occurred_at=_occurred(2026, 7, 12),
    )

    first_cycle.refresh_from_db()
    second_cycle.refresh_from_db()
    assert selectors.cycle_remaining_due(cycle=first_cycle) == Decimal("0.00")
    assert selectors.cycle_remaining_due(cycle=second_cycle) == Decimal("50.00")
    assert first_cycle.status == BillingCycle.Status.PAID
    assert second_cycle.status == BillingCycle.Status.PARTIALLY_PAID
    assert selectors.unallocated_repayment_amount(transaction=first_repayment) == Decimal("0.00")
    first_repayment.refresh_from_db()
    assert first_repayment.is_financial_locked is True

    excess = services.create_credit_card_repayment(
        profile=profile,
        source_account=bank,
        credit_card_account=profile.account,
        amount=Decimal("100.00"),
        occurred_at=_occurred(2026, 7, 12),
    )
    second_cycle.refresh_from_db()
    assert second_cycle.status == BillingCycle.Status.PAID
    assert selectors.unallocated_repayment_amount(transaction=excess) == Decimal("50.00")
    assert selectors.overpayment(profile=profile) == Decimal("50.00")
    assert selectors.issued_unpaid_amount(profile=profile) == Decimal("0.00")


@pytest.mark.django_db
def test_repayment_allocation_is_idempotent(profile, bank, expense_category):
    _purchase(
        profile=profile,
        expense_category=expense_category,
        amount=Decimal("100.00"),
        occurred_at=_occurred(2026, 7, 10),
    )
    _issue(BillingCycle.objects.get(), Decimal("100.00"))
    repayment = services.create_credit_card_repayment(
        profile=profile,
        source_account=bank,
        credit_card_account=profile.account,
        amount=Decimal("40.00"),
        occurred_at=_occurred(2026, 7, 12),
    )

    assert services.allocate_repayment_transaction(profile=profile, repayment=repayment) == Decimal(
        "0.00"
    )
    assert repayment.billing_cycle_items.count() == 1


@pytest.mark.django_db
def test_refund_does_not_reduce_issued_due_until_confirmed(profile, expense_category):
    purchase = _purchase(
        profile=profile,
        expense_category=expense_category,
        amount=Decimal("100.00"),
        occurred_at=_occurred(2026, 7, 10),
    )
    cycle = BillingCycle.objects.get()
    _issue(cycle, Decimal("100.00"))
    refund = ledger_services.create_refund(
        original_transaction=purchase,
        amount=Decimal("30.00"),
        occurred_at=_occurred(2026, 7, 12),
    )

    assert selectors.current_liability(profile=profile) == Decimal("70.00")
    assert selectors.cycle_remaining_due(cycle=cycle) == Decimal("100.00")

    services.confirm_refund_credit(cycle=cycle, refund=refund)

    refund.refresh_from_db()
    assert selectors.cycle_remaining_due(cycle=cycle) == Decimal("70.00")
    assert refund.is_financial_locked is True


@pytest.mark.django_db
def test_unbilled_and_overdue_status_formulas(profile, expense_category):
    _purchase(
        profile=profile,
        expense_category=expense_category,
        amount=Decimal("80.00"),
        occurred_at=_occurred(2026, 7, 10),
    )
    cycle = BillingCycle.objects.get()
    _issue(cycle, Decimal("50.00"))
    cycle.refresh_from_db()

    assert selectors.issued_unpaid_amount(profile=profile) == Decimal("50.00")
    assert selectors.unbilled_amount(profile=profile) == Decimal("30.00")
    assert (
        selectors.effective_cycle_status(cycle=cycle, as_of=date(2026, 8, 6))
        == BillingCycle.Status.OVERDUE
    )


def _token(response):
    return response.context["submission_token"]


@pytest.mark.django_db
def test_profile_purchase_issue_and_repayment_pages(
    authenticated_client, bank, card, expense_category
):
    settings_response = authenticated_client.post(
        reverse("credit:profile-settings"),
        {
            "account": card.id,
            "credit_limit": "10000.00",
            "personal_monthly_limit": "2000.00",
            "statement_day": 15,
            "due_day": 5,
        },
    )
    assert settings_response.status_code == 302

    purchase_url = reverse("credit:purchase-create")
    purchase_response = authenticated_client.post(
        purchase_url,
        {
            "amount": "100.00",
            "occurred_at": "2026-07-10T12:00",
            "channel": Transaction.Channel.OTHER,
            "counterparty": "测试商家",
            "note": "",
            "account": card.id,
            "category": expense_category.id,
            "submission_token": _token(authenticated_client.get(purchase_url)),
        },
    )
    assert purchase_response.status_code == 302
    cycle = BillingCycle.objects.get()

    issue_url = reverse("credit:cycle-issue", args=[cycle.id])
    issue_response = authenticated_client.post(
        issue_url,
        {
            "official_statement_amount": "100.00",
            "official_due_amount": "100.00",
            "due_date": "2026-08-05",
            "note": "",
            "submission_token": _token(authenticated_client.get(issue_url)),
        },
    )
    assert issue_response.status_code == 302

    repayment_url = reverse("credit:repayment-create")
    repayment_response = authenticated_client.post(
        repayment_url,
        {
            "amount": "100.00",
            "occurred_at": "2026-07-12T12:00",
            "source_account": bank.id,
            "credit_card_account": card.id,
            "channel": Transaction.Channel.BANK,
            "note": "",
            "submission_token": _token(authenticated_client.get(repayment_url)),
        },
    )
    cycle.refresh_from_db()
    assert repayment_response.status_code == 302
    assert cycle.status == BillingCycle.Status.PAID
    assert authenticated_client.get(reverse("credit:overview")).status_code == 200
