from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate, TruncMonth
from django.utils import timezone

from apps.budgets import selectors as budget_selectors
from apps.budgets.models import PlannedCashFlow
from apps.core import selectors as core_selectors
from apps.credit import selectors as credit_selectors
from apps.installments import selectors as installment_selectors
from apps.installments.models import InstallmentItem, InstallmentPlan
from apps.ledger import selectors as ledger_selectors
from apps.ledger.models import Transaction

from . import services

ZERO = Decimal("0.00")


@dataclass(frozen=True)
class UpcomingItem:
    due_date: date
    kind: str
    title: str
    amount: Decimal | None
    counts_as_payable: bool = True
    note: str = ""


@dataclass(frozen=True)
class DashboardSnapshot:
    month: date
    as_of: date
    liquid_assets: Decimal
    credit_liability: Decimal
    net_funds: Decimal
    allocatable_funds: Decimal
    monthly_income: Decimal
    budget: dict
    reserve_balance: Decimal
    next_due_date: date | None
    next_due_amount: Decimal
    credit_status: services.CreditRiskStatus
    budget_status: str
    installment_status: services.ForecastRiskStatus
    alerts: tuple[services.RiskAlert, ...]
    upcoming_items: tuple[UpcomingItem, ...]

    @property
    def credit_status_label(self) -> str:
        return services.CREDIT_RISK_LABELS[self.credit_status]

    @property
    def credit_status_tone(self) -> str:
        # 视觉状态与既有风险枚举一一对应，文字标签仍是主要信息载体。
        return services.CREDIT_RISK_TONES[self.credit_status]

    @property
    def budget_status_label(self) -> str:
        return {
            "NOT_SET": "未设置预算",
            "OK": "正常",
            "WARNING": "接近预算",
            "OVER": "超过预算",
        }[self.budget_status]

    @property
    def budget_status_tone(self) -> str:
        return {
            "NOT_SET": "neutral",
            "OK": "success",
            "WARNING": "warning",
            "OVER": "danger",
        }[self.budget_status]

    @property
    def installment_status_label(self) -> str:
        return services.FORECAST_RISK_LABELS[self.installment_status]

    @property
    def installment_status_tone(self) -> str:
        return services.FORECAST_RISK_TONES[self.installment_status]


@dataclass(frozen=True)
class IncomeExpensePoint:
    period: date
    income: Decimal
    expense: Decimal

    @property
    def surplus(self) -> Decimal:
        return self.income - self.expense


@dataclass(frozen=True)
class AmountPoint:
    period: date
    label: str
    amount: Decimal


@dataclass(frozen=True)
class BudgetExecutionRow:
    category: str
    budget_amount: Decimal
    occupied: Decimal
    remaining: Decimal
    usage_percentage: Decimal | None
    status: str

    @property
    def status_label(self) -> str:
        return {"OK": "正常", "WARNING": "提醒", "OVER": "超支"}[self.status]


@dataclass(frozen=True)
class InstallmentBurdenRow:
    month: date
    actual: Decimal
    planned: Decimal
    total: Decimal


@dataclass(frozen=True)
class SavingsTargetRow:
    month: date
    target: Decimal
    income_expense_surplus: Decimal
    completion_percentage: Decimal | None


@dataclass(frozen=True)
class CreditReport:
    configured: bool
    credit_limit: Decimal
    personal_limit: Decimal
    current_liability: Decimal
    issued_unpaid: Decimal
    unbilled: Decimal
    monthly_purchases: tuple[AmountPoint, ...]


@dataclass(frozen=True)
class ReportSnapshot:
    date_from: date
    date_to: date
    transaction_count: int
    monthly: tuple[IncomeExpensePoint, ...]
    categories: tuple[AmountPoint, ...]
    daily: tuple[AmountPoint, ...]
    net_funds: tuple[AmountPoint, ...]
    budget_month: date
    budget_summary: dict
    budget_rows: tuple[BudgetExecutionRow, ...]
    credit: CreditReport
    installments: tuple[InstallmentBurdenRow, ...]
    savings: tuple[SavingsTargetRow, ...]


def _as_of_end(value: date):
    return timezone.make_aware(datetime.combine(value, time.max), timezone.get_current_timezone())


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _shift_month(value: date, offset: int) -> date:
    index = value.year * 12 + value.month - 1 + offset
    year, zero_based_month = divmod(index, 12)
    return date(year, zero_based_month + 1, 1)


def _months_between(date_from: date, date_to: date) -> tuple[date, ...]:
    first = _month_start(date_from)
    last = _month_start(date_to)
    months: list[date] = []
    offset = 0
    while True:
        month = _shift_month(first, offset)
        months.append(month)
        if month == last:
            return tuple(months)
        offset += 1


def _budget_status(snapshot: dict) -> str:
    budget = snapshot["budget"]
    if budget is None:
        return "NOT_SET"
    category_rows = budget_selectors.category_budget_rows(budget=budget)
    if category_rows:
        if any(row["status"] == "OVER" for row in category_rows):
            return "OVER"
        if any(row["status"] == "WARNING" for row in category_rows):
            return "WARNING"
        return "OK"
    usage = snapshot["usage_percentage"]
    if usage is None:
        return "OK"
    warning, over = core_selectors.budget_thresholds()
    if warning < over:
        if usage >= over:
            return "OVER"
        if usage >= warning:
            return "WARNING"
    # 没有明细项目时，提醒阈值等于系统上限（通常为 100%）时正好 100% 视为正常。
    return "OVER" if warning == over and usage > over else "OK"


def upcoming_items(
    *, as_of: date, alerts: tuple[services.RiskAlert, ...] | None = None
) -> tuple[UpcomingItem, ...]:
    date_to = as_of + timedelta(days=30)
    items: list[UpcomingItem] = []
    profile = credit_selectors.active_profile()
    if profile is not None:
        for cycle in credit_selectors.unpaid_cycles(
            profile=profile, date_from=as_of, date_to=date_to
        ):
            remaining = credit_selectors.cycle_remaining_due(cycle=cycle)
            if remaining > 0:
                items.append(
                    UpcomingItem(cycle.due_date, "信用卡还款", "本期信用卡账单", remaining)
                )

    for installment in installment_selectors.upcoming_items(date_from=as_of, date_to=date_to):
        if installment.status == InstallmentItem.Status.PLANNED:
            items.append(
                UpcomingItem(
                    installment.due_date,
                    "分期应付款",
                    f"{installment.plan.product_name} 第 {installment.sequence_number} 期",
                    installment.planned_amount,
                )
            )
        elif (
            installment.plan.source_type == InstallmentPlan.SourceType.CREDIT_CARD
            and installment.billing_cycle_id is not None
        ):
            items.append(
                UpcomingItem(
                    installment.due_date,
                    "分期来源",
                    f"{installment.plan.product_name} 第 {installment.sequence_number} 期",
                    installment.actual_amount,
                    counts_as_payable=False,
                    note="已计入信用卡账单，不重复累计应付款。",
                )
            )

    for occurrence in budget_selectors.upcoming_occurrences(date_from=as_of, date_to=date_to):
        if occurrence.plan.direction == PlannedCashFlow.Direction.EXPENSE:
            items.append(
                UpcomingItem(
                    occurrence.due_date,
                    "固定支出",
                    occurrence.plan.name,
                    occurrence.planned_amount,
                )
            )
        elif occurrence.plan.reliability == PlannedCashFlow.Reliability.CERTAIN:
            items.append(
                UpcomingItem(
                    occurrence.due_date,
                    "确定预计收入",
                    occurrence.plan.name,
                    occurrence.planned_amount,
                    counts_as_payable=False,
                )
            )

    alert_rows = services.risk_alerts(as_of=as_of) if alerts is None else alerts
    for alert in alert_rows:
        if alert.code in {"CATEGORY_WARNING", "CATEGORY_OVER"}:
            items.append(
                UpcomingItem(
                    as_of,
                    "预算预警",
                    alert.message,
                    alert.amount,
                    counts_as_payable=False,
                )
            )
    return tuple(
        sorted(items, key=lambda item: (item.due_date, item.kind, item.title, item.amount or ZERO))
    )


def dashboard_snapshot(*, month: date, as_of: date) -> DashboardSnapshot:
    month = _month_start(month)
    as_of_end = _as_of_end(as_of)
    profile = credit_selectors.active_profile()
    next_cycle = credit_selectors.next_unpaid_cycle(profile=profile) if profile else None
    capacity = services.repayment_capacity(as_of=as_of, cycle=next_cycle)
    forecast = services.forecast_cash_flow(as_of=as_of, month_count=6)
    alerts = services.risk_alerts(as_of=as_of, forecast_months=6)
    budget = budget_selectors.monthly_snapshot(month=month)
    net_funds = ledger_selectors.current_net_funds(as_of=as_of_end)
    allocatable_funds = (
        net_funds - budget["allocatable_remaining"] - budget["savings_target"]
    )
    return DashboardSnapshot(
        month=month,
        as_of=as_of,
        liquid_assets=ledger_selectors.liquid_assets(as_of=as_of_end),
        credit_liability=ledger_selectors.current_liabilities(as_of=as_of_end),
        net_funds=net_funds,
        allocatable_funds=allocatable_funds,
        monthly_income=ledger_selectors.monthly_income(month=month),
        budget=budget,
        reserve_balance=budget_selectors.reserve_balance(as_of=as_of),
        next_due_date=next_cycle.due_date if next_cycle else None,
        next_due_amount=(
            credit_selectors.cycle_remaining_due(cycle=next_cycle) if next_cycle else ZERO
        ),
        credit_status=capacity.status,
        budget_status=_budget_status(budget),
        installment_status=forecast.overall_status,
        alerts=alerts,
        upcoming_items=upcoming_items(as_of=as_of, alerts=alerts),
    )


def _month_key(value) -> date:
    if isinstance(value, datetime):
        value = timezone.localtime(value).date() if timezone.is_aware(value) else value.date()
    return value.replace(day=1)


def report_snapshot(
    *,
    date_from: date,
    date_to: date,
    transaction_type: str | None = None,
    account_id: int | None = None,
    category_id: int | None = None,
    budget_month: date | None = None,
) -> ReportSnapshot:
    transactions = ledger_selectors.reporting_transactions(
        date_from=date_from,
        date_to=date_to,
        transaction_type=transaction_type,
        account_id=account_id,
        category_id=category_id,
    )
    months = _months_between(date_from, date_to)
    monthly_values = {month: [ZERO, ZERO] for month in months}
    for row in (
        transactions.annotate(period=TruncMonth("occurred_at"))
        .values("period")
        .annotate(
            income=Sum("amount", filter=Q(transaction_type=Transaction.TransactionType.INCOME)),
            expenses=Sum("amount", filter=Q(transaction_type=Transaction.TransactionType.EXPENSE)),
            refunds=Sum("amount", filter=Q(transaction_type=Transaction.TransactionType.REFUND)),
        )
    ):
        key = _month_key(row["period"])
        monthly_values[key] = [
            row["income"] or ZERO,
            (row["expenses"] or ZERO) - (row["refunds"] or ZERO),
        ]
    monthly = tuple(
        IncomeExpensePoint(month, values[0], values[1]) for month, values in monthly_values.items()
    )

    category_rows = (
        transactions.filter(
            transaction_type__in=[
                Transaction.TransactionType.EXPENSE,
                Transaction.TransactionType.REFUND,
            ]
        )
        .values("category__name")
        .annotate(
            expenses=Sum("amount", filter=Q(transaction_type=Transaction.TransactionType.EXPENSE)),
            refunds=Sum("amount", filter=Q(transaction_type=Transaction.TransactionType.REFUND)),
        )
        .order_by("category__name")
    )
    categories = tuple(
        AmountPoint(
            date_from,
            row["category__name"] or "未分类",
            (row["expenses"] or ZERO) - (row["refunds"] or ZERO),
        )
        for row in category_rows
    )

    daily_values = {
        date_from + timedelta(days=offset): ZERO for offset in range((date_to - date_from).days + 1)
    }
    for row in (
        transactions.filter(
            transaction_type__in=[
                Transaction.TransactionType.EXPENSE,
                Transaction.TransactionType.REFUND,
            ]
        )
        .annotate(period=TruncDate("occurred_at"))
        .values("period")
        .annotate(
            expenses=Sum("amount", filter=Q(transaction_type=Transaction.TransactionType.EXPENSE)),
            refunds=Sum("amount", filter=Q(transaction_type=Transaction.TransactionType.REFUND)),
        )
    ):
        daily_values[row["period"]] = (row["expenses"] or ZERO) - (row["refunds"] or ZERO)
    daily = tuple(AmountPoint(day, day.isoformat(), amount) for day, amount in daily_values.items())
    net_funds = tuple(
        AmountPoint(day, day.isoformat(), amount)
        for day, amount in ledger_selectors.net_funds_series(dates=list(daily_values))
    )

    selected_budget_month = _month_start(budget_month or date_to)
    budget_summary = budget_selectors.monthly_snapshot(month=selected_budget_month)
    budget = budget_summary["budget"]
    budget_rows = (
        tuple(
            BudgetExecutionRow(
                row["category"].name,
                row["budget_amount"],
                row["occupancy"],
                row["budget_amount"] - row["occupancy"],
                row["usage_percentage"],
                row["status"],
            )
            for row in budget_selectors.category_budget_rows(budget=budget)
        )
        if budget
        else ()
    )

    profile = credit_selectors.active_profile()
    credit_month_values = {month: ZERO for month in months}
    if profile:
        for row in credit_selectors.monthly_purchase_rows(
            profile=profile, date_from=date_from, date_to=date_to
        ):
            credit_month_values[_month_key(row["month"])] = row["total"] or ZERO
        credit = CreditReport(
            True,
            profile.credit_limit,
            profile.personal_monthly_limit,
            credit_selectors.current_liability(profile=profile),
            credit_selectors.issued_unpaid_amount(profile=profile),
            credit_selectors.unbilled_amount(profile=profile),
            tuple(
                AmountPoint(month, month.strftime("%Y-%m"), amount)
                for month, amount in credit_month_values.items()
            ),
        )
    else:
        credit = CreditReport(False, ZERO, ZERO, ZERO, ZERO, ZERO, ())

    planned, posted, refunds = installment_selectors.burden_rows(
        month_from=months[0], month_to=months[-1]
    )
    burden_values = {month: [ZERO, ZERO] for month in months}
    for row in planned:
        burden_values[row["due_month"]][1] = row["total"] or ZERO
    for row in posted:
        burden_values[row["ledger_transaction__budget_month"]][0] = row["total"] or ZERO
    for row in refunds:
        burden_values[row["budget_month"]][0] -= row["total"] or ZERO
    installments = tuple(
        InstallmentBurdenRow(month, values[0], values[1], values[0] + values[1])
        for month, values in burden_values.items()
    )

    monthly_by_month = {row.period: row for row in monthly}
    savings_rows = []
    for item in budget_selectors.budget_range(month_from=months[0], month_to=months[-1]):
        point = monthly_by_month.get(item.month, IncomeExpensePoint(item.month, ZERO, ZERO))
        completion = (
            (point.surplus / item.savings_target * Decimal("100.00")).quantize(Decimal("0.01"))
            if item.savings_target > 0
            else None
        )
        savings_rows.append(
            SavingsTargetRow(item.month, item.savings_target, point.surplus, completion)
        )
    return ReportSnapshot(
        date_from=date_from,
        date_to=date_to,
        transaction_count=transactions.aggregate(count=Count("id"))["count"],
        monthly=monthly,
        categories=categories,
        daily=daily,
        net_funds=net_funds,
        budget_month=selected_budget_month,
        budget_summary=budget_summary,
        budget_rows=budget_rows,
        credit=credit,
        installments=installments,
        savings=tuple(savings_rows),
    )
