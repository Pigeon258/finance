import calendar
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction as db_transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.accounts.models import Account
from apps.core import selectors as core_selectors
from apps.ledger import services as ledger_services
from apps.ledger.models import Category, Transaction

from .models import (
    CategoryBudget,
    MonthlyBudget,
    PlannedCashFlow,
    PlannedCashFlowOccurrence,
    ReserveMovement,
)

MONEY_QUANTUM = Decimal("0.01")
MAX_MONEY = Decimal("999999999999.99")


def _validate_money(
    value: Decimal, *, allow_zero: bool = False, allow_negative: bool = False
) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValidationError("金额必须使用有限 Decimal。")
    if abs(value) > MAX_MONEY or value != value.quantize(MONEY_QUANTUM):
        raise ValidationError("金额必须精确到分且不超出范围。")
    if not allow_negative and (value < 0 or (value == 0 and not allow_zero)):
        raise ValidationError("金额必须大于零。")
    if allow_negative and value == 0 and not allow_zero:
        raise ValidationError("金额不得为零。")


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _shift_month(value: date, offset: int) -> tuple[int, int]:
    index = value.year * 12 + value.month - 1 + offset
    return divmod(index, 12)[0], divmod(index, 12)[1] + 1


def _clamped_date(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


@db_transaction.atomic
def save_monthly_budget(
    *,
    month: date,
    total_expense_budget: Decimal | None = None,
    savings_target: Decimal = Decimal("0.00"),
    minimum_safety_buffer: Decimal = Decimal("0.00"),
    note: str = "",
) -> MonthlyBudget:
    month = _month_start(month)
    for value in [savings_target, minimum_safety_buffer]:
        _validate_money(value, allow_zero=True)
    budget = MonthlyBudget.objects.select_for_update().filter(month=month).first()
    if budget is None:
        budget = MonthlyBudget(month=month, total_expense_budget=Decimal("0.00"))
    if total_expense_budget is not None:
        _validate_money(total_expense_budget, allow_zero=True)
        budget.total_expense_budget = total_expense_budget
    budget.savings_target = savings_target
    budget.minimum_safety_buffer = minimum_safety_buffer
    budget.note = note
    budget.full_clean()
    budget.save()
    return budget


@db_transaction.atomic
def refresh_monthly_budget_total(*, monthly_budget: MonthlyBudget) -> MonthlyBudget:
    monthly_budget = MonthlyBudget.objects.select_for_update().get(pk=monthly_budget.pk)
    total = sum(
        (
            item.budget_amount
            for item in CategoryBudget.objects.filter(monthly_budget=monthly_budget)
        ),
        Decimal("0.00"),
    )
    monthly_budget.total_expense_budget = total
    monthly_budget.full_clean()
    monthly_budget.save(update_fields=["total_expense_budget", "updated_at"])
    return monthly_budget


def _get_or_create_monthly_budget(*, month: date) -> MonthlyBudget:
    month = _month_start(month)
    budget, _ = MonthlyBudget.objects.get_or_create(
        month=month,
        defaults={
            "total_expense_budget": Decimal("0.00"),
            "savings_target": Decimal("0.00"),
            "minimum_safety_buffer": Decimal("0.00"),
        },
    )
    return budget


@db_transaction.atomic
def create_budget_item(
    *,
    month: date,
    name: str,
    category: Category,
    budget_amount: Decimal,
    warning_threshold: Decimal | None = None,
    sort_order: int = 0,
) -> CategoryBudget:
    monthly_budget = MonthlyBudget.objects.select_for_update().filter(
        month=_month_start(month)
    ).first()
    if monthly_budget is None:
        monthly_budget = _get_or_create_monthly_budget(month=month)
        monthly_budget = MonthlyBudget.objects.select_for_update().get(pk=monthly_budget.pk)
    item = _save_budget_item(
        monthly_budget=monthly_budget,
        name=name,
        category=category,
        budget_amount=budget_amount,
        warning_threshold=warning_threshold,
        sort_order=sort_order,
    )
    refresh_monthly_budget_total(monthly_budget=monthly_budget)
    return item


@db_transaction.atomic
def update_budget_item(
    *,
    item: CategoryBudget,
    name: str,
    category: Category,
    budget_amount: Decimal,
    warning_threshold: Decimal | None = None,
    sort_order: int | None = None,
) -> CategoryBudget:
    item = CategoryBudget.objects.select_for_update().get(pk=item.pk)
    item = _save_budget_item(
        monthly_budget=item.monthly_budget,
        name=name,
        category=category,
        budget_amount=budget_amount,
        warning_threshold=warning_threshold,
        sort_order=sort_order if sort_order is not None else item.sort_order,
        item=item,
    )
    refresh_monthly_budget_total(monthly_budget=item.monthly_budget)
    return item


def _save_budget_item(
    *,
    monthly_budget: MonthlyBudget,
    name: str,
    category: Category,
    budget_amount: Decimal,
    warning_threshold: Decimal | None,
    sort_order: int,
    item: CategoryBudget | None = None,
) -> CategoryBudget:
    name = name.strip()
    if not name:
        raise ValidationError("预算项目名称不能为空。")
    _validate_money(budget_amount)
    if not category.is_active or category.category_type != Category.CategoryType.EXPENSE:
        raise ValidationError("预算项目必须使用启用的支出分类。")
    default_warning, over_threshold = core_selectors.budget_thresholds()
    warning_threshold = warning_threshold if warning_threshold is not None else default_warning
    _validate_money(warning_threshold, allow_zero=True)
    if warning_threshold > over_threshold:
        raise ValidationError("提醒阈值不得高于系统超支阈值。")
    item = item or CategoryBudget(monthly_budget=monthly_budget)
    item.monthly_budget = monthly_budget
    item.name = name
    item.category = category
    item.budget_amount = budget_amount
    item.warning_threshold = warning_threshold
    item.sort_order = sort_order
    item.full_clean()
    item.save()
    return item


@db_transaction.atomic
def delete_budget_item(*, item: CategoryBudget) -> None:
    item = CategoryBudget.objects.select_for_update().get(pk=item.pk)
    monthly_budget = item.monthly_budget
    item.delete()
    refresh_monthly_budget_total(monthly_budget=monthly_budget)


@db_transaction.atomic
def save_category_budget(
    *,
    monthly_budget: MonthlyBudget,
    category: Category,
    budget_amount: Decimal,
    warning_threshold: Decimal | None = None,
    name: str | None = None,
    sort_order: int = 0,
) -> CategoryBudget:
    """Legacy compatibility helper; budget items are now named and may share a category."""
    item_name = (name or category.name).strip()
    item = (
        CategoryBudget.objects.select_for_update()
        .filter(monthly_budget=monthly_budget, name=item_name)
        .first()
    )
    saved = _save_budget_item(
        monthly_budget=monthly_budget,
        name=item_name,
        category=category,
        budget_amount=budget_amount,
        warning_threshold=warning_threshold,
        sort_order=sort_order if item is None else item.sort_order,
        item=item,
    )
    refresh_monthly_budget_total(monthly_budget=monthly_budget)
    return saved


@db_transaction.atomic
def copy_monthly_budget(*, source_month: date, target_month: date) -> MonthlyBudget:
    source_month = _month_start(source_month)
    target_month = _month_start(target_month)
    if source_month == target_month:
        raise ValidationError("来源月份和目标月份不能相同。")
    source = MonthlyBudget.objects.prefetch_related("category_budgets__category").get(
        month=source_month
    )
    target, _ = MonthlyBudget.objects.get_or_create(
        month=target_month,
        defaults={
            "total_expense_budget": source.total_expense_budget,
            "savings_target": source.savings_target,
            "minimum_safety_buffer": source.minimum_safety_buffer,
            "note": source.note,
        },
    )
    target = MonthlyBudget.objects.select_for_update().get(pk=target.pk)
    for source_item in source.category_budgets.all():
        CategoryBudget.objects.get_or_create(
            monthly_budget=target,
            name=source_item.name,
            defaults={
                "category": source_item.category,
                "budget_amount": source_item.budget_amount,
                "warning_threshold": source_item.warning_threshold,
                "sort_order": source_item.sort_order,
            },
        )
    return refresh_monthly_budget_total(monthly_budget=target)


def _reserve_total() -> Decimal:
    totals = ReserveMovement.objects.aggregate(
        contributions=Sum("amount", filter=Q(movement_type="CONTRIBUTION")),
        withdrawals=Sum("amount", filter=Q(movement_type="WITHDRAWAL")),
        corrections=Sum("amount", filter=Q(movement_type="CORRECTION")),
    )
    return (
        (totals["contributions"] or Decimal("0.00"))
        - (totals["withdrawals"] or Decimal("0.00"))
        + (totals["corrections"] or Decimal("0.00"))
    )


@db_transaction.atomic
def record_reserve_movement(
    *,
    movement_type: str,
    amount: Decimal,
    occurred_on: date,
    related_transaction: Transaction | None = None,
    note: str = "",
) -> ReserveMovement:
    list(ReserveMovement.objects.select_for_update().values_list("pk", flat=True))
    if movement_type == ReserveMovement.MovementType.CORRECTION:
        _validate_money(amount, allow_negative=True)
        delta = amount
    elif movement_type == ReserveMovement.MovementType.CONTRIBUTION:
        _validate_money(amount)
        delta = amount
    elif movement_type == ReserveMovement.MovementType.WITHDRAWAL:
        _validate_money(amount)
        delta = -amount
    else:
        raise ValidationError("不支持的储备变动类型。")
    if _reserve_total() + delta < 0:
        raise ValidationError("储备变动后累计储备不得为负数。")
    movement = ReserveMovement(
        movement_type=movement_type,
        amount=amount,
        occurred_on=occurred_on,
        related_transaction=related_transaction,
        note=note,
    )
    movement.full_clean()
    movement.save()
    return movement


def _validate_plan_category(*, direction: str, category: Category) -> None:
    expected = (
        Category.CategoryType.INCOME
        if direction == PlannedCashFlow.Direction.INCOME
        else Category.CategoryType.EXPENSE
    )
    if not category.is_active or category.category_type != expected:
        raise ValidationError("计划方向与启用分类不匹配。")


@db_transaction.atomic
def create_planned_cash_flow(
    *,
    name: str,
    direction: str,
    amount: Decimal,
    category: Category,
    default_account: Account | None,
    reliability: str,
    recurrence_type: str,
    start_date: date,
    end_date: date | None = None,
    day_of_month: int | None = None,
    is_active: bool = True,
    note: str = "",
) -> PlannedCashFlow:
    _validate_money(amount)
    _validate_plan_category(direction=direction, category=category)
    if not name.strip():
        raise ValidationError("计划名称不能为空。")
    if end_date and end_date < start_date:
        raise ValidationError("结束日期不得早于开始日期。")
    day_of_month = day_of_month or start_date.day
    if not 1 <= day_of_month <= 31:
        raise ValidationError("每月日期必须在 1 到 31 之间。")
    if direction == PlannedCashFlow.Direction.EXPENSE:
        reliability = PlannedCashFlow.Reliability.CERTAIN
    if default_account is not None and not default_account.is_active:
        raise ValidationError("默认账户必须启用。")
    plan = PlannedCashFlow(
        name=name.strip(),
        direction=direction,
        amount=amount,
        category=category,
        default_account=default_account,
        reliability=reliability,
        recurrence_type=recurrence_type,
        start_date=start_date,
        end_date=end_date,
        day_of_month=day_of_month,
        is_active=is_active,
        note=note,
    )
    plan.full_clean()
    plan.save()
    horizon_year, horizon_month = _shift_month(start_date, 11)
    horizon = date(horizon_year, horizon_month, calendar.monthrange(horizon_year, horizon_month)[1])
    generate_occurrences(plan=plan, through_date=min(end_date, horizon) if end_date else horizon)
    return plan


def _candidate_dates(*, plan: PlannedCashFlow, through_date: date, from_date: date | None = None):
    if plan.recurrence_type == PlannedCashFlow.RecurrenceType.ONE_TIME:
        if plan.start_date <= through_date and (
            plan.end_date is None or plan.start_date <= plan.end_date
        ):
            if from_date is None or plan.start_date >= from_date:
                yield plan.start_date
        return
    if plan.recurrence_type == PlannedCashFlow.RecurrenceType.MONTHLY:
        offset = 0
        while True:
            year, month = _shift_month(plan.start_date, offset)
            candidate = _clamped_date(year, month, plan.day_of_month)
            if candidate < plan.start_date:
                offset += 1
                continue
            if candidate > through_date or (plan.end_date and candidate > plan.end_date):
                break
            if from_date is None or candidate >= from_date:
                yield candidate
            offset += 1
        return
    year = plan.start_date.year
    while True:
        candidate = _clamped_date(year, plan.start_date.month, plan.day_of_month)
        if candidate >= plan.start_date:
            if candidate > through_date or (plan.end_date and candidate > plan.end_date):
                break
            if from_date is None or candidate >= from_date:
                yield candidate
        year += 1


@db_transaction.atomic
def generate_occurrences(
    *,
    plan: PlannedCashFlow,
    through_date: date,
    from_date: date | None = None,
) -> list[PlannedCashFlowOccurrence]:
    plan = PlannedCashFlow.objects.select_for_update().get(pk=plan.pk)
    if not plan.is_active:
        return []
    if through_date < plan.start_date:
        return []
    if from_date is not None and through_date < from_date:
        return []
    generated = []
    for due_date in _candidate_dates(
        plan=plan, through_date=through_date, from_date=from_date
    ):
        occurrence, created = PlannedCashFlowOccurrence.objects.get_or_create(
            plan=plan, due_date=due_date, defaults={"planned_amount": plan.amount}
        )
        if created:
            generated.append(occurrence)
    return generated


def ensure_active_plan_occurrences(*, as_of: date, horizon_months: int = 12) -> int:
    """Ensure every active recurring plan has occurrences from the current month forward."""
    from_date = as_of.replace(day=1)
    horizon_year, horizon_month = _shift_month(from_date, horizon_months - 1)
    through_date = date(
        horizon_year,
        horizon_month,
        calendar.monthrange(horizon_year, horizon_month)[1],
    )
    generated_count = 0
    for plan in PlannedCashFlow.objects.filter(is_active=True):
        generated_count += len(
            generate_occurrences(
                plan=plan, through_date=through_date, from_date=from_date
            )
        )
    return generated_count


@db_transaction.atomic
def confirm_occurrence(
    *,
    occurrence: PlannedCashFlowOccurrence,
    account: Account | None,
    actual_amount: Decimal,
    occurred_at,
    note: str = "",
) -> PlannedCashFlowOccurrence:
    _validate_money(actual_amount)
    occurrence = (
        PlannedCashFlowOccurrence.objects.select_for_update()
        .select_related("plan__category", "plan__default_account")
        .get(pk=occurrence.pk)
    )
    if occurrence.status != PlannedCashFlowOccurrence.Status.PLANNED:
        raise ValidationError("该计划事项已经处理，不能重复确认。")
    plan = occurrence.plan
    account = account or plan.default_account
    if account is None or not account.is_active:
        raise ValidationError("确认计划事项时必须选择启用账户。")
    common = {
        "account": account,
        "category": plan.category,
        "amount": actual_amount,
        "occurred_at": occurred_at,
        "channel": Transaction.Channel.OTHER,
        "counterparty": plan.name,
        "note": note,
        "source": Transaction.Source.MANUAL,
    }
    if plan.direction == PlannedCashFlow.Direction.INCOME:
        ledger_transaction = ledger_services.create_income(**common)
    else:
        ledger_transaction = ledger_services.create_expense(
            **common, budget_month=occurrence.due_date.replace(day=1)
        )
    occurrence.status = PlannedCashFlowOccurrence.Status.CONFIRMED
    occurrence.linked_transaction = ledger_transaction
    occurrence.confirmed_at = timezone.now()
    occurrence.note = note
    occurrence.full_clean()
    occurrence.save()
    return occurrence


@db_transaction.atomic
def set_occurrence_status(
    *, occurrence: PlannedCashFlowOccurrence, status: str, note: str = ""
) -> PlannedCashFlowOccurrence:
    occurrence = PlannedCashFlowOccurrence.objects.select_for_update().get(pk=occurrence.pk)
    if occurrence.status != PlannedCashFlowOccurrence.Status.PLANNED:
        raise ValidationError("只有待发生事项可以跳过或标记过期。")
    if status not in [
        PlannedCashFlowOccurrence.Status.SKIPPED,
        PlannedCashFlowOccurrence.Status.EXPIRED,
    ]:
        raise ValidationError("不支持的计划事项状态。")
    occurrence.status = status
    occurrence.note = note
    occurrence.full_clean()
    occurrence.save(update_fields=["status", "note", "updated_at"])
    return occurrence


@db_transaction.atomic
def set_plan_active(*, plan: PlannedCashFlow, is_active: bool) -> PlannedCashFlow:
    plan = PlannedCashFlow.objects.select_for_update().get(pk=plan.pk)
    plan.is_active = is_active
    plan.save(update_fields=["is_active", "updated_at"])
    return plan
