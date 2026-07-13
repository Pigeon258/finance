from datetime import date
from decimal import Decimal

from django.db.models import QuerySet, Sum

from apps.ledger import selectors as ledger_selectors
from apps.ledger.models import Transaction

from .models import BillingCycle, BillingCycleItem, CreditCardProfile


def active_profile() -> CreditCardProfile | None:
    return CreditCardProfile.objects.select_related("account").filter(is_active=True).first()


def cycle_calculated_statement_amount(*, cycle: BillingCycle) -> Decimal:
    positive_types = [
        BillingCycleItem.ItemType.CHARGE,
        BillingCycleItem.ItemType.INSTALLMENT,
        BillingCycleItem.ItemType.FEE,
        BillingCycleItem.ItemType.ADJUSTMENT,
    ]
    charges = cycle.items.filter(
        item_type__in=positive_types,
        transaction__status=Transaction.Status.ACTIVE,
    ).aggregate(total=Sum("allocated_amount"))["total"] or Decimal("0.00")
    return max(charges, Decimal("0.00"))


def cycle_due_base(*, cycle: BillingCycle) -> Decimal:
    if cycle.official_due_amount is not None:
        return cycle.official_due_amount
    return cycle_calculated_statement_amount(cycle=cycle)


def cycle_repaid_amount(*, cycle: BillingCycle) -> Decimal:
    return cycle.items.filter(
        item_type=BillingCycleItem.ItemType.REPAYMENT,
        transaction__status=Transaction.Status.ACTIVE,
    ).aggregate(total=Sum("allocated_amount"))["total"] or Decimal("0.00")


def cycle_confirmed_credit_amount(*, cycle: BillingCycle) -> Decimal:
    return cycle.items.filter(
        item_type=BillingCycleItem.ItemType.REFUND,
        transaction__status=Transaction.Status.ACTIVE,
    ).aggregate(total=Sum("allocated_amount"))["total"] or Decimal("0.00")


def cycle_remaining_due(*, cycle: BillingCycle) -> Decimal:
    return max(
        cycle_due_base(cycle=cycle)
        - cycle_repaid_amount(cycle=cycle)
        - cycle_confirmed_credit_amount(cycle=cycle),
        Decimal("0.00"),
    )


def issued_unpaid_amount(*, profile: CreditCardProfile) -> Decimal:
    return sum(
        (
            cycle_remaining_due(cycle=cycle)
            for cycle in profile.billing_cycles.filter(
                status__in=[
                    BillingCycle.Status.ISSUED,
                    BillingCycle.Status.PARTIALLY_PAID,
                    BillingCycle.Status.OVERDUE,
                ]
            )
        ),
        Decimal("0.00"),
    )


def current_liability(*, profile: CreditCardProfile) -> Decimal:
    return max(ledger_selectors.account_balance(account=profile.account), Decimal("0.00"))


def overpayment(*, profile: CreditCardProfile) -> Decimal:
    return max(-ledger_selectors.account_balance(account=profile.account), Decimal("0.00"))


def unbilled_amount(*, profile: CreditCardProfile) -> Decimal:
    return max(
        current_liability(profile=profile) - issued_unpaid_amount(profile=profile), Decimal("0.00")
    )


def unallocated_repayment_amount(*, transaction: Transaction) -> Decimal:
    allocated = transaction.billing_cycle_items.filter(
        item_type=BillingCycleItem.ItemType.REPAYMENT
    ).aggregate(total=Sum("allocated_amount"))["total"] or Decimal("0.00")
    return max(transaction.amount - allocated, Decimal("0.00"))


def next_unpaid_cycle(*, profile: CreditCardProfile) -> BillingCycle | None:
    for cycle in profile.billing_cycles.exclude(status=BillingCycle.Status.OPEN).order_by(
        "due_date", "id"
    ):
        if cycle_remaining_due(cycle=cycle) > 0:
            return cycle
    return None


def unpaid_cycles(*, profile: CreditCardProfile) -> QuerySet[BillingCycle]:
    return (
        profile.billing_cycles.exclude(status=BillingCycle.Status.OPEN)
        .exclude(status=BillingCycle.Status.PAID)
        .order_by("due_date", "id")
    )


def monthly_purchase_amount(*, profile: CreditCardProfile, month: date) -> Decimal:
    return profile.account.transaction_entries.filter(
        transaction__status=Transaction.Status.ACTIVE,
        transaction__transaction_type=Transaction.TransactionType.EXPENSE,
        transaction__occurred_at__year=month.year,
        transaction__occurred_at__month=month.month,
    ).aggregate(total=Sum("transaction__amount"))["total"] or Decimal("0.00")


def effective_cycle_status(*, cycle: BillingCycle, as_of: date) -> str:
    if cycle.status == BillingCycle.Status.OPEN:
        return BillingCycle.Status.OPEN
    remaining = cycle_remaining_due(cycle=cycle)
    if remaining == 0:
        return BillingCycle.Status.PAID
    if cycle.due_date < as_of:
        return BillingCycle.Status.OVERDUE
    if cycle_repaid_amount(cycle=cycle) + cycle_confirmed_credit_amount(cycle=cycle) > 0:
        return BillingCycle.Status.PARTIALLY_PAID
    return BillingCycle.Status.ISSUED
