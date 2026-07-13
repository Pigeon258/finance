from datetime import date
from decimal import Decimal

from django.db.models import Sum

from apps.ledger.models import Transaction

from .models import InstallmentItem, InstallmentPlan


def plan_list():
    return InstallmentPlan.objects.select_related("category").prefetch_related("items")


def plan_detail(*, plan_id: int) -> InstallmentPlan:
    return (
        InstallmentPlan.objects.select_related("category")
        .prefetch_related(
            "items__ledger_transaction__related_transactions",
            "items__billing_cycle",
            "adjustments__installment_item",
            "adjustments__related_transaction",
        )
        .get(pk=plan_id)
    )


def remaining_commitment(*, plan: InstallmentPlan | None = None) -> Decimal:
    queryset = InstallmentItem.objects.filter(status=InstallmentItem.Status.PLANNED)
    if plan is not None:
        queryset = queryset.filter(plan=plan)
    return queryset.aggregate(total=Sum("planned_amount"))["total"] or Decimal("0.00")


def future_month_summary():
    return (
        InstallmentItem.objects.filter(status=InstallmentItem.Status.PLANNED)
        .values("due_month")
        .annotate(total=Sum("planned_amount"))
        .order_by("due_month")
    )


def monthly_occupancy(*, month: date, category_id: int | None = None) -> dict[str, Decimal]:
    month = month.replace(day=1)
    planned = InstallmentItem.objects.filter(status=InstallmentItem.Status.PLANNED, due_month=month)
    posted = InstallmentItem.objects.filter(
        status=InstallmentItem.Status.POSTED,
        ledger_transaction__status=Transaction.Status.ACTIVE,
        ledger_transaction__budget_month=month,
    )
    if category_id is not None:
        planned = planned.filter(plan__category_id=category_id)
        posted = posted.filter(plan__category_id=category_id)
    planned_total = planned.aggregate(total=Sum("planned_amount"))["total"] or Decimal("0.00")
    actual_gross = posted.aggregate(total=Sum("ledger_transaction__amount"))["total"] or Decimal(
        "0.00"
    )
    refunds = Transaction.objects.filter(
        transaction_type=Transaction.TransactionType.REFUND,
        status=Transaction.Status.ACTIVE,
        related_transaction__installment_item__in=posted,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    actual = actual_gross - refunds
    return {"actual": actual, "planned": planned_total, "total": actual + planned_total}
