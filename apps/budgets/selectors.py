from datetime import date
from decimal import Decimal

from django.db.models import Q, Sum

from apps.core import selectors as core_selectors
from apps.installments import selectors as installment_selectors
from apps.ledger import selectors as ledger_selectors
from apps.ledger.models import Category, Transaction

from .models import (
    CategoryBudget,
    MonthlyBudget,
    PlannedCashFlow,
    PlannedCashFlowOccurrence,
    ReserveMovement,
)


def monthly_budget(*, month: date) -> MonthlyBudget | None:
    return (
        MonthlyBudget.objects.prefetch_related("category_budgets__category")
        .filter(month=month.replace(day=1))
        .first()
    )


def reserve_balance(*, as_of: date | None = None) -> Decimal:
    queryset = ReserveMovement.objects.all()
    if as_of is not None:
        queryset = queryset.filter(occurred_on__lte=as_of)
    totals = queryset.aggregate(
        contributions=Sum(
            "amount", filter=Q(movement_type=ReserveMovement.MovementType.CONTRIBUTION)
        ),
        withdrawals=Sum("amount", filter=Q(movement_type=ReserveMovement.MovementType.WITHDRAWAL)),
        corrections=Sum("amount", filter=Q(movement_type=ReserveMovement.MovementType.CORRECTION)),
    )
    return (
        (totals["contributions"] or Decimal("0.00"))
        - (totals["withdrawals"] or Decimal("0.00"))
        + (totals["corrections"] or Decimal("0.00"))
    )


def reserve_movements():
    return ReserveMovement.objects.select_related("related_transaction")


def planned_cash_flows():
    return PlannedCashFlow.objects.select_related("category", "default_account").prefetch_related(
        "occurrences"
    )


def occurrence_list(*, month: date | None = None):
    queryset = PlannedCashFlowOccurrence.objects.select_related(
        "plan__category", "plan__default_account", "linked_transaction"
    )
    if month is not None:
        month = month.replace(day=1)
        if month.month == 12:
            next_month = date(month.year + 1, 1, 1)
        else:
            next_month = date(month.year, month.month + 1, 1)
        queryset = queryset.filter(due_date__gte=month, due_date__lt=next_month)
    return queryset


def planned_expense_commitment(*, month: date, category: Category | None = None) -> Decimal:
    month = month.replace(day=1)
    queryset = PlannedCashFlowOccurrence.objects.filter(
        plan__direction=PlannedCashFlow.Direction.EXPENSE,
        status=PlannedCashFlowOccurrence.Status.PLANNED,
        due_date__year=month.year,
        due_date__month=month.month,
    )
    if category is not None:
        queryset = queryset.filter(plan__category=category)
    return queryset.aggregate(total=Sum("planned_amount"))["total"] or Decimal("0.00")


def planned_expense_commitment_between(
    *, date_from: date, date_to: date, necessary_only: bool = False
) -> Decimal:
    queryset = PlannedCashFlowOccurrence.objects.filter(
        plan__direction=PlannedCashFlow.Direction.EXPENSE,
        status=PlannedCashFlowOccurrence.Status.PLANNED,
        due_date__gte=date_from,
        due_date__lte=date_to,
    )
    if necessary_only:
        queryset = queryset.filter(plan__category__necessity=Category.Necessity.NECESSARY)
    return queryset.aggregate(total=Sum("planned_amount"))["total"] or Decimal("0.00")


def planned_income(
    *, month: date, reliability: str | None = None, due_on_or_after: date | None = None
) -> Decimal:
    month = month.replace(day=1)
    queryset = PlannedCashFlowOccurrence.objects.filter(
        plan__direction=PlannedCashFlow.Direction.INCOME,
        status=PlannedCashFlowOccurrence.Status.PLANNED,
        due_date__year=month.year,
        due_date__month=month.month,
    )
    if reliability is not None:
        queryset = queryset.filter(plan__reliability=reliability)
    if due_on_or_after is not None:
        queryset = queryset.filter(due_date__gte=due_on_or_after)
    return queryset.aggregate(total=Sum("planned_amount"))["total"] or Decimal("0.00")


def _confirmed_fixed_actual(*, month: date, category: Category | None = None) -> Decimal:
    month = month.replace(day=1)
    occurrences = PlannedCashFlowOccurrence.objects.filter(
        plan__direction=PlannedCashFlow.Direction.EXPENSE,
        status=PlannedCashFlowOccurrence.Status.CONFIRMED,
        linked_transaction__status=Transaction.Status.ACTIVE,
        linked_transaction__budget_month=month,
    )
    if category is not None:
        occurrences = occurrences.filter(plan__category=category)
    gross = occurrences.aggregate(total=Sum("linked_transaction__amount"))["total"] or Decimal(
        "0.00"
    )
    refunds = Transaction.objects.filter(
        transaction_type=Transaction.TransactionType.REFUND,
        status=Transaction.Status.ACTIVE,
        related_transaction__planned_cash_flow_occurrence__in=occurrences,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    return gross - refunds


def monthly_breakdown(*, month: date, category: Category | None = None) -> dict[str, Decimal]:
    month = month.replace(day=1)
    actual_total = ledger_selectors.monthly_net_expense(month=month, category=category)
    fixed_actual = _confirmed_fixed_actual(month=month, category=category)
    fixed_planned = planned_expense_commitment(month=month, category=category)
    installment = installment_selectors.monthly_occupancy(
        month=month, category_id=category.id if category else None
    )
    ordinary = actual_total - fixed_actual - installment["actual"]
    fixed = fixed_actual + fixed_planned
    return {
        "ordinary_expense": ordinary,
        "fixed_actual": fixed_actual,
        "fixed_planned": fixed_planned,
        "fixed_expense": fixed,
        "installment_actual": installment["actual"],
        "installment_planned": installment["planned"],
        "installment": installment["total"],
        "actual_expense": actual_total,
        "planned_commitment": fixed_planned + installment["planned"],
    }


def monthly_snapshot(*, month: date) -> dict[str, Decimal | MonthlyBudget | None]:
    month = month.replace(day=1)
    budget = monthly_budget(month=month)
    breakdown = monthly_breakdown(month=month)
    savings_target = budget.savings_target if budget else Decimal("0.00")
    total_budget = budget.total_expense_budget if budget else Decimal("0.00")
    total_occupancy = breakdown["actual_expense"] + breakdown["planned_commitment"] + savings_target
    remaining = total_budget - total_occupancy
    usage = (
        (total_occupancy / total_budget * Decimal("100.00")).quantize(Decimal("0.01"))
        if total_budget > 0
        else None
    )
    return {
        "budget": budget,
        **breakdown,
        "savings_target": savings_target,
        "total_budget": total_budget,
        "total_occupancy": total_occupancy,
        "remaining": remaining,
        "usage_percentage": usage,
    }


def category_budget_status(*, category_budget: CategoryBudget) -> dict[str, Decimal | str | None]:
    occupancy = monthly_breakdown(
        month=category_budget.monthly_budget.month, category=category_budget.category
    )
    amount = (
        occupancy["actual_expense"] + occupancy["fixed_planned"] + occupancy["installment_planned"]
    )
    budget_amount = category_budget.budget_amount
    if budget_amount == 0:
        usage = None
        status = "OVER" if amount > 0 else "OK"
    else:
        usage = (amount / budget_amount * Decimal("100.00")).quantize(Decimal("0.01"))
        _, over_threshold = core_selectors.budget_thresholds()
        if amount * Decimal("100.00") >= budget_amount * over_threshold:
            status = "OVER"
        elif amount * Decimal("100.00") >= budget_amount * category_budget.warning_threshold:
            status = "WARNING"
        else:
            status = "OK"
    return {"occupancy": amount, "usage_percentage": usage, "status": status}


def category_budget_rows(*, budget: MonthlyBudget):
    return [
        {"category_budget": item, **category_budget_status(category_budget=item)}
        for item in budget.category_budgets.select_related("category")
    ]
