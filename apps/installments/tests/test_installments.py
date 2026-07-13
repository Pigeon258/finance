from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse

from apps.accounts.models import Account
from apps.credit import services as credit_services
from apps.credit.models import BillingCycleItem
from apps.installments import selectors, services
from apps.installments.models import InstallmentAdjustment, InstallmentItem, InstallmentPlan
from apps.ledger import selectors as ledger_selectors
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
    account.initial_balance = Decimal("5000.00")
    account.save(update_fields=["initial_balance"])
    return account


@pytest.fixture
def card():
    return Account.objects.get(account_type=Account.AccountType.CREDIT_CARD)


@pytest.fixture
def category():
    return Category.objects.get(name="数码产品")


@pytest.fixture
def profile(card):
    return credit_services.save_profile(
        account=card,
        credit_limit=Decimal("10000.00"),
        personal_monthly_limit=Decimal("3000.00"),
        statement_day=15,
        due_day=31,
    )


def _occurred(year, month, day):
    return datetime(year, month, day, 12, 0, tzinfo=TZ)


def _platform_plan(category, **overrides):
    data = {
        "product_name": "笔记本电脑",
        "purchase_date": date(2026, 1, 10),
        "original_price": Decimal("1000.00"),
        "category": category,
        "source_type": InstallmentPlan.SourceType.PLATFORM,
        "installment_count": 3,
        "default_installment_amount": Decimal("333.33"),
        "total_repayment_amount": Decimal("1000.00"),
        "first_due_date": date(2026, 1, 31),
    }
    data.update(overrides)
    return services.create_plan(**data)


@pytest.mark.django_db
def test_platform_plan_generates_clamped_dates_tail_and_no_financial_fact(category, bank):
    before = ledger_selectors.account_balance(account=bank)
    plan = _platform_plan(category)

    items = list(plan.items.all())
    assert [item.due_date for item in items] == [
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 31),
    ]
    assert [item.due_month for item in items] == [
        date(2026, 1, 1),
        date(2026, 2, 1),
        date(2026, 3, 1),
    ]
    assert [item.planned_amount for item in items] == [
        Decimal("333.33"),
        Decimal("333.33"),
        Decimal("333.34"),
    ]
    assert Transaction.objects.count() == 0
    assert ledger_selectors.account_balance(account=bank) == before
    assert selectors.remaining_commitment(plan=plan) == Decimal("1000.00")


@pytest.mark.django_db
def test_credit_plan_uses_expected_due_dates_across_year(category, profile):
    plan = services.create_plan(
        product_name="手机",
        purchase_date=date(2026, 11, 1),
        original_price=Decimal("600.00"),
        category=category,
        source_type=InstallmentPlan.SourceType.CREDIT_CARD,
        installment_count=3,
        default_installment_amount=Decimal("200.00"),
        first_due_month=date(2026, 12, 1),
    )

    assert list(plan.items.values_list("due_date", flat=True)) == [
        date(2026, 12, 31),
        date(2027, 1, 31),
        date(2027, 2, 28),
    ]
    assert Transaction.objects.count() == 0


@pytest.mark.django_db
def test_platform_post_converts_commitment_once_and_uses_due_month(category, bank):
    plan = _platform_plan(category)
    item = plan.items.get(sequence_number=1)

    services.post_item(
        item=item,
        actual_amount=Decimal("330.00"),
        occurred_at=_occurred(2025, 12, 20),
        account=bank,
    )

    item.refresh_from_db()
    assert item.status == InstallmentItem.Status.POSTED
    assert item.ledger_transaction.budget_month == date(2026, 1, 1)
    assert ledger_selectors.account_balance(account=bank) == Decimal("4670.00")
    occupancy = selectors.monthly_occupancy(month=date(2026, 1, 1))
    assert occupancy == {
        "actual": Decimal("330.00"),
        "planned": Decimal("0.00"),
        "total": Decimal("330.00"),
    }
    with pytest.raises(ValidationError):
        services.post_item(
            item=item,
            actual_amount=Decimal("330.00"),
            occurred_at=_occurred(2025, 12, 20),
            account=bank,
        )
    assert Transaction.objects.count() == 1


@pytest.mark.django_db
def test_credit_post_adds_liability_and_installment_cycle_item(category, profile):
    plan = services.create_plan(
        product_name="手机",
        purchase_date=date(2026, 6, 1),
        original_price=Decimal("200.00"),
        category=category,
        source_type=InstallmentPlan.SourceType.CREDIT_CARD,
        installment_count=1,
        default_installment_amount=Decimal("200.00"),
        first_due_month=date(2026, 8, 1),
    )
    item = services.post_item(
        item=plan.items.get(),
        actual_amount=Decimal("200.00"),
        occurred_at=_occurred(2026, 7, 10),
    )

    cycle_item = BillingCycleItem.objects.get(transaction=item.ledger_transaction)
    assert cycle_item.item_type == BillingCycleItem.ItemType.INSTALLMENT
    assert item.billing_cycle == cycle_item.billing_cycle
    assert item.due_date == date(2026, 8, 31)
    assert ledger_selectors.account_balance(account=profile.account) == Decimal("200.00")
    plan.refresh_from_db()
    assert plan.status == InstallmentPlan.Status.COMPLETED


@pytest.mark.django_db
def test_refund_is_allocated_to_posted_item_and_future_reduction_is_not_transaction(category, bank):
    plan = _platform_plan(category, default_installment_amount=Decimal("400.00"))
    first = services.post_item(
        item=plan.items.get(sequence_number=1),
        actual_amount=Decimal("400.00"),
        occurred_at=_occurred(2026, 1, 31),
        account=bank,
    )
    services.start_refund(plan=plan)
    with pytest.raises(ValidationError):
        services.post_item(
            item=plan.items.get(sequence_number=2),
            actual_amount=Decimal("400.00"),
            occurred_at=_occurred(2026, 2, 28),
            account=bank,
        )

    refund = services.refund_posted_item(
        item=first,
        amount=Decimal("100.00"),
        occurred_at=_occurred(2026, 2, 5),
        account=bank,
    )
    future = plan.items.get(sequence_number=2)
    services.adjust_planned_item(
        item=future,
        new_amount=Decimal("200.00"),
        new_due_date=date(2026, 2, 27),
        effective_date=date(2026, 2, 5),
        note="平台减少后续扣款",
    )

    assert refund.related_transaction == first.ledger_transaction
    assert refund.budget_month == first.due_month
    assert Transaction.objects.count() == 2
    assert (
        InstallmentAdjustment.objects.filter(
            adjustment_type=InstallmentAdjustment.AdjustmentType.REFUND
        ).count()
        == 2
    )
    assert selectors.monthly_occupancy(month=date(2026, 1, 1))["actual"] == Decimal("300.00")
    services.finish_refund(plan=plan)
    plan.refresh_from_db()
    assert plan.status == InstallmentPlan.Status.ACTIVE


@pytest.mark.django_db
def test_over_refund_rolls_back_without_adjustment(category, bank):
    plan = _platform_plan(
        category,
        installment_count=1,
        default_installment_amount=Decimal("100.00"),
        total_repayment_amount=Decimal("100.00"),
    )
    item = services.post_item(
        item=plan.items.get(),
        actual_amount=Decimal("100.00"),
        occurred_at=_occurred(2026, 1, 31),
        account=bank,
    )
    services.start_refund(plan=plan)
    services.refund_posted_item(
        item=item,
        amount=Decimal("60.00"),
        occurred_at=_occurred(2026, 2, 1),
        account=bank,
    )
    adjustments_before = InstallmentAdjustment.objects.count()
    with pytest.raises(ValidationError):
        services.refund_posted_item(
            item=item,
            amount=Decimal("41.00"),
            occurred_at=_occurred(2026, 2, 2),
            account=bank,
        )
    assert InstallmentAdjustment.objects.count() == adjustments_before
    assert (
        Transaction.objects.filter(transaction_type=Transaction.TransactionType.REFUND).count() == 1
    )


@pytest.mark.django_db
def test_early_settlement_cancels_future_items_and_records_actual_transaction(category, bank):
    plan = _platform_plan(category)
    services.early_settle(
        plan=plan,
        amount=Decimal("950.00"),
        occurred_at=_occurred(2026, 1, 20),
        account=bank,
        note="优惠结清",
    )

    plan.refresh_from_db()
    assert plan.status == InstallmentPlan.Status.EARLY_SETTLED
    assert plan.total_repayment_amount == Decimal("950.00")
    assert not plan.items.filter(status=InstallmentItem.Status.PLANNED).exists()
    adjustment = plan.adjustments.get(
        adjustment_type=InstallmentAdjustment.AdjustmentType.EARLY_SETTLEMENT
    )
    assert adjustment.related_transaction.amount == Decimal("950.00")
    assert adjustment.installment_item.status == InstallmentItem.Status.POSTED
    assert adjustment.installment_item.ledger_transaction == adjustment.related_transaction
    assert adjustment.amount_delta == Decimal("-50.00")
    services.start_refund(plan=plan)
    plan.refresh_from_db()
    assert plan.status == InstallmentPlan.Status.REFUND_PROCESSING


@pytest.mark.django_db
def test_plan_sequence_constraint_and_due_month_validation(category):
    plan = _platform_plan(category)
    original = plan.items.get(sequence_number=1)
    duplicate = InstallmentItem(
        plan=plan,
        sequence_number=1,
        due_date=date(2026, 4, 30),
        due_month=date(2026, 4, 1),
        planned_amount=Decimal("1.00"),
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        duplicate.save()
    original.due_month = date(2026, 2, 1)
    with pytest.raises(ValidationError):
        original.full_clean()


@pytest.mark.django_db
def test_plan_pages_and_duplicate_submission(authenticated_client, category):
    response = authenticated_client.get(reverse("installments:index"))
    assert response.status_code == 200
    create_page = authenticated_client.get(reverse("installments:create"))
    token = create_page.context["submission_token"]
    data = {
        "submission_token": token,
        "product_name": "耳机",
        "purchase_date": "2026-01-01",
        "original_price": "300.00",
        "category": category.id,
        "source_type": InstallmentPlan.SourceType.PLATFORM,
        "installment_count": "3",
        "default_installment_amount": "100.00",
        "total_repayment_amount": "300.00",
        "first_due_date": "2026-01-31",
        "note": "",
    }
    response = authenticated_client.post(reverse("installments:create"), data)
    assert response.status_code == 302
    assert InstallmentPlan.objects.filter(product_name="耳机").count() == 1
    response = authenticated_client.post(reverse("installments:create"), data)
    assert response.status_code == 302
    assert InstallmentPlan.objects.filter(product_name="耳机").count() == 1
