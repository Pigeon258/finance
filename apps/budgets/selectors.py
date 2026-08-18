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

BUDGET_STATUS_LABELS = {
    "OK": "正常",
    "WARNING": "提醒",
    "OVER": "超支",
}
BUDGET_STATUS_TONES = {
    "OK": "success",
    "WARNING": "warning",
    "OVER": "danger",
}


def _budget_status_parts(status: str) -> dict[str, str]:
    return {
        "status": status,
        "status_label": BUDGET_STATUS_LABELS[status],
        "status_tone": BUDGET_STATUS_TONES[status],
    }


def _category_status_parts(
    *,
    budget_amount: Decimal,
    occupancy: Decimal,
    warning_threshold: Decimal,
    over_threshold: Decimal,
) -> dict[str, Decimal | str | None]:
    if budget_amount == 0:
        usage = None
        status = "OVER" if occupancy > 0 else "OK"
        return {"usage_percentage": usage, **_budget_status_parts(status)}

    usage = (occupancy / budget_amount * Decimal("100.00")).quantize(Decimal("0.01"))
    if warning_threshold < over_threshold:
        if occupancy * Decimal("100.00") >= budget_amount * over_threshold:
            status = "OVER"
        elif occupancy * Decimal("100.00") >= budget_amount * warning_threshold:
            status = "WARNING"
        else:
            status = "OK"
    else:
        # When the user sets the reminder threshold to the system cap (usually 100%),
        # only spending above the budget is treated as over; hitting exactly 100% is OK.
        status = "OVER" if occupancy * Decimal("100.00") > budget_amount * over_threshold else "OK"
    return {"usage_percentage": usage, **_budget_status_parts(status)}


def monthly_budget(*, month: date) -> MonthlyBudget | None:
    return (
        MonthlyBudget.objects.prefetch_related("category_budgets__category")
        .filter(month=month.replace(day=1))
        .first()
    )


def pending_savings_carryover(*, current_month: date) -> MonthlyBudget | None:
    current_month = current_month.replace(day=1)
    previous_month = (
        date(current_month.year - 1, 12, 1)
        if current_month.month == 1
        else date(current_month.year, current_month.month - 1, 1)
    )
    return (
        MonthlyBudget.objects.filter(
            month=previous_month,
            savings_target__gt=0,
            savings_settled_at__isnull=True,
        )
        .order_by("month")
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


def occurrence_list(*, month: date | None = None, date_from: date | None = None):
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
    if date_from is not None:
        queryset = queryset.filter(due_date__gte=date_from)
    return queryset


def upcoming_occurrences(*, date_from: date, date_to: date):
    return PlannedCashFlowOccurrence.objects.filter(
        status=PlannedCashFlowOccurrence.Status.PLANNED,
        due_date__gte=date_from,
        due_date__lte=date_to,
    ).select_related("plan__category")


def budget_range(*, month_from: date, month_to: date):
    return MonthlyBudget.objects.filter(
        month__gte=month_from.replace(day=1),
        month__lte=month_to.replace(day=1),
    ).order_by("month")


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
    full_breakdown = monthly_breakdown(month=month)
    category_rows = category_budget_rows(budget=budget) if budget else []
    if category_rows:
        # 有分类预算项目时，只有已配置预算的分类进入预算口径；其他消费仍由账本记录并扣减账户余额。
        breakdown = {
            key: sum(
                (
                    monthly_breakdown(month=month, category=row["category"])[key]
                    for row in category_rows
                ),
                Decimal("0.00"),
            )
            for key in full_breakdown
        }
        unbudgeted_expense = full_breakdown["actual_expense"] - breakdown["actual_expense"]
    else:
        # 没有分类预算项目时，兼容月度总预算覆盖全部支出的旧口径。
        breakdown = full_breakdown
        unbudgeted_expense = (
            full_breakdown["actual_expense"] if budget is None else Decimal("0.00")
        )
    savings_target = budget.savings_target if budget else Decimal("0.00")
    total_budget = budget.total_expense_budget if budget else Decimal("0.00")
    total_occupancy = breakdown["actual_expense"] + breakdown["planned_commitment"]
    allocatable_remaining = (
        total_budget - breakdown["actual_expense"] - breakdown["planned_commitment"]
    )
    remaining = total_budget - total_occupancy
    usage = (
        (total_occupancy / total_budget * Decimal("100.00")).quantize(Decimal("0.01"))
        if total_budget > 0
        else None
    )
    return {
        "budget": budget,
        **breakdown,
        "unbudgeted_expense": unbudgeted_expense,
        "savings_target": savings_target,
        "total_budget": total_budget,
        "allocatable_remaining": allocatable_remaining,
        "total_occupancy": total_occupancy,
        "remaining": remaining,
        "usage_percentage": usage,
    }


def budget_item_rows(*, month: date):
    month = month.replace(day=1)
    return list(
        CategoryBudget.objects.filter(monthly_budget__month=month)
        .select_related("category")
        .order_by("sort_order", "id")
    )


def category_budget_status(*, category_budget: CategoryBudget) -> dict[str, Decimal | str | None]:
    """Compatibility helper: return the status of the category containing this budget item."""
    month = category_budget.monthly_budget.month
    category = category_budget.category
    category_amount = (
        CategoryBudget.objects.filter(
            monthly_budget__month=month, category=category
        ).aggregate(total=Sum("budget_amount"))["total"]
        or Decimal("0.00")
    )
    occupancy = monthly_breakdown(month=month, category=category)
    amount = (
        occupancy["actual_expense"] + occupancy["fixed_planned"] + occupancy["installment_planned"]
    )
    _, over_threshold = core_selectors.budget_thresholds()
    return {
        "occupancy": amount,
        **_category_status_parts(
            budget_amount=category_amount,
            occupancy=amount,
            warning_threshold=category_budget.warning_threshold,
            over_threshold=over_threshold,
        ),
    }


def category_budget_rows(*, budget: MonthlyBudget):
    month = budget.month
    items = list(budget.category_budgets.select_related("category"))
    by_category: dict[int, dict[str, Decimal]] = {}
    for item in items:
        group = by_category.setdefault(
            item.category_id,
            {"budget_amount": Decimal("0.00"), "warning_threshold": item.warning_threshold},
        )
        group["budget_amount"] += item.budget_amount
        group["warning_threshold"] = min(group["warning_threshold"], item.warning_threshold)

    actual_rows = (
        ledger_selectors.active_transactions()
        .filter(
            transaction_type__in=[
                Transaction.TransactionType.EXPENSE,
                Transaction.TransactionType.REFUND,
            ],
            budget_month=month,
        )
        .values("category_id")
        .annotate(
            expenses=Sum("amount", filter=Q(transaction_type=Transaction.TransactionType.EXPENSE)),
            refunds=Sum("amount", filter=Q(transaction_type=Transaction.TransactionType.REFUND)),
        )
    )
    actual_by_category = {
        row["category_id"]: (row["expenses"] or Decimal("0.00"))
        - (row["refunds"] or Decimal("0.00"))
        for row in actual_rows
    }
    fixed_by_category = {
        row["plan__category_id"]: row["total"] or Decimal("0.00")
        for row in PlannedCashFlowOccurrence.objects.filter(
            plan__direction=PlannedCashFlow.Direction.EXPENSE,
            status=PlannedCashFlowOccurrence.Status.PLANNED,
            due_date__year=month.year,
            due_date__month=month.month,
        )
        .values("plan__category_id")
        .annotate(total=Sum("planned_amount"))
    }
    installment_by_category = {
        row["plan__category_id"]: row["total"] or Decimal("0.00")
        for row in installment_selectors.planned_by_category(month=month)
    }

    _, over_threshold = core_selectors.budget_thresholds()
    categories = {
        category.id: category
        for category in Category.objects.filter(id__in=by_category.keys())
    }
    rows = []
    for category_id, group in by_category.items():
        category = categories[category_id]
        budget_amount = group["budget_amount"]
        warning_threshold = group["warning_threshold"]
        occupancy = (
            actual_by_category.get(category_id, Decimal("0.00"))
            + fixed_by_category.get(category_id, Decimal("0.00"))
            + installment_by_category.get(category_id, Decimal("0.00"))
        )
        rows.append(
            {
                "category": category,
                "budget_amount": budget_amount,
                "occupancy": occupancy,
                "remaining": budget_amount - occupancy,
                **_category_status_parts(
                    budget_amount=budget_amount,
                    occupancy=occupancy,
                    warning_threshold=warning_threshold,
                    over_threshold=over_threshold,
                ),
            }
        )
    return rows
