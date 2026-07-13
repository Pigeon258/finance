import calendar
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.budgets import selectors as budget_selectors
from apps.credit import selectors as credit_selectors
from apps.credit.models import BillingCycle
from apps.installments import selectors as installment_selectors
from apps.ledger import selectors as ledger_selectors
from apps.ledger.models import Category

MONEY_QUANTUM = Decimal("0.01")
MAX_MONEY = Decimal("999999999999.99")


class CreditRiskStatus(StrEnum):
    SAFE = "SAFE"
    USE_RESERVE = "USE_RESERVE"
    DANGER = "DANGER"


class ForecastRiskStatus(StrEnum):
    SAFE = "SAFE"
    AFFORDABLE = "AFFORDABLE"
    USE_RESERVE = "USE_RESERVE"
    HIGH_RISK = "HIGH_RISK"


class AlertLevel(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    DANGER = "DANGER"


@dataclass(frozen=True)
class RepaymentCapacity:
    as_of: date
    due_date: date | None
    required_due: Decimal
    liquid_assets: Decimal
    reserve_balance: Decimal
    necessary_protection: Decimal
    regular_available: Decimal
    final_available: Decimal
    status: CreditRiskStatus


@dataclass(frozen=True)
class MonthlyForecast:
    month: date
    regular_start_with_savings: Decimal
    regular_start_without_savings: Decimal
    final_start: Decimal
    certain_income: Decimal
    determined_commitment: Decimal
    expected_expense: Decimal
    savings_target: Decimal
    safety_buffer: Decimal
    regular_end_with_savings: Decimal
    regular_end_without_savings: Decimal
    final_end_without_savings: Decimal
    status: ForecastRiskStatus


@dataclass(frozen=True)
class ForecastResult:
    as_of: date
    months: tuple[MonthlyForecast, ...]
    overall_status: ForecastRiskStatus


@dataclass(frozen=True)
class InstallmentPreview:
    baseline: ForecastResult
    simulated: ForecastResult
    additional_commitments: tuple[tuple[date, Decimal], ...]


@dataclass(frozen=True)
class RiskAlert:
    level: AlertLevel
    code: str
    message: str
    amount: Decimal | None = None
    due_date: date | None = None


def _validate_money(
    value: Decimal, *, allow_zero: bool = False, allow_negative: bool = False
) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValidationError("风险计算金额必须使用有限 Decimal。")
    if abs(value) > MAX_MONEY or value != value.quantize(MONEY_QUANTUM):
        raise ValidationError("风险计算金额必须精确到分且不超出范围。")
    if (value < 0 and not allow_negative) or (value == 0 and not allow_zero):
        raise ValidationError("风险计算金额必须为非负数。")


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _shift_month(value: date, offset: int) -> date:
    index = value.year * 12 + value.month - 1 + offset
    year, zero_based_month = divmod(index, 12)
    return date(year, zero_based_month + 1, 1)


def _month_end(month: date) -> date:
    return date(month.year, month.month, calendar.monthrange(month.year, month.month)[1])


def calculate_repayment_capacity(
    *,
    as_of: date,
    due_date: date | None,
    required_due: Decimal,
    liquid_assets: Decimal,
    reserve_balance: Decimal,
    necessary_protection: Decimal,
) -> RepaymentCapacity:
    for value in [required_due, reserve_balance, necessary_protection]:
        _validate_money(value, allow_zero=True)
    _validate_money(liquid_assets, allow_zero=True, allow_negative=True)
    regular_available = max(liquid_assets - reserve_balance - necessary_protection, Decimal("0.00"))
    final_available = max(liquid_assets - necessary_protection, Decimal("0.00"))
    if regular_available >= required_due:
        status = CreditRiskStatus.SAFE
    elif final_available >= required_due:
        status = CreditRiskStatus.USE_RESERVE
    else:
        status = CreditRiskStatus.DANGER
    return RepaymentCapacity(
        as_of=as_of,
        due_date=due_date,
        required_due=required_due,
        liquid_assets=liquid_assets,
        reserve_balance=reserve_balance,
        necessary_protection=necessary_protection,
        regular_available=regular_available,
        final_available=final_available,
        status=status,
    )


def _necessary_budget_remaining(*, month: date) -> Decimal:
    budget = budget_selectors.monthly_budget(month=month)
    if budget is None:
        return Decimal("0.00")
    total = Decimal("0.00")
    for category_budget in budget.category_budgets.select_related("category"):
        if category_budget.category.necessity != Category.Necessity.NECESSARY:
            continue
        actual = budget_selectors.monthly_breakdown(month=month, category=category_budget.category)[
            "actual_expense"
        ]
        total += max(category_budget.budget_amount - actual, Decimal("0.00"))
    return total


def necessary_protection_funds(*, as_of: date, due_date: date) -> Decimal:
    if due_date < as_of:
        return Decimal("0.00")
    current_month = _month_start(as_of)
    final_month = _month_start(due_date)
    protection = Decimal("0.00")
    offset = 0
    while True:
        month = _shift_month(current_month, offset)
        range_start = max(as_of, month)
        range_end = min(due_date, _month_end(month))
        necessary_budget = _necessary_budget_remaining(month=month)
        fixed = budget_selectors.planned_expense_commitment_between(
            date_from=range_start, date_to=range_end, necessary_only=True
        )
        installments = installment_selectors.planned_commitment_between(
            date_from=range_start, date_to=range_end, necessary_only=True
        )
        protection += max(necessary_budget, fixed + installments)
        if month == final_month:
            break
        offset += 1
    return protection


def _as_of_end(as_of: date):
    return timezone.make_aware(datetime.combine(as_of, time.max), timezone.get_current_timezone())


def repayment_capacity(*, as_of: date, cycle: BillingCycle | None = None) -> RepaymentCapacity:
    profile = credit_selectors.active_profile()
    if cycle is None and profile is not None:
        cycle = credit_selectors.next_unpaid_cycle(profile=profile)
    required_due = credit_selectors.cycle_remaining_due(cycle=cycle) if cycle else Decimal("0.00")
    due_date = cycle.due_date if cycle else None
    protection = (
        necessary_protection_funds(as_of=as_of, due_date=due_date)
        if due_date is not None
        else Decimal("0.00")
    )
    return calculate_repayment_capacity(
        as_of=as_of,
        due_date=due_date,
        required_due=required_due,
        liquid_assets=ledger_selectors.liquid_assets(as_of=_as_of_end(as_of)),
        reserve_balance=budget_selectors.reserve_balance(as_of=as_of),
        necessary_protection=protection,
    )


def classify_forecast_month(
    *,
    regular_end_with_savings: Decimal,
    regular_end_without_savings: Decimal,
    final_end_without_savings: Decimal,
    safety_buffer: Decimal,
) -> ForecastRiskStatus:
    if regular_end_with_savings >= safety_buffer:
        return ForecastRiskStatus.SAFE
    if regular_end_without_savings >= safety_buffer:
        return ForecastRiskStatus.AFFORDABLE
    if final_end_without_savings >= safety_buffer:
        return ForecastRiskStatus.USE_RESERVE
    return ForecastRiskStatus.HIGH_RISK


def _worst_status(months: list[MonthlyForecast]) -> ForecastRiskStatus:
    severity = {
        ForecastRiskStatus.SAFE: 0,
        ForecastRiskStatus.AFFORDABLE: 1,
        ForecastRiskStatus.USE_RESERVE: 2,
        ForecastRiskStatus.HIGH_RISK: 3,
    }
    if not months:
        return ForecastRiskStatus.SAFE
    return max((item.status for item in months), key=severity.__getitem__)


def forecast_cash_flow(
    *,
    as_of: date,
    month_count: int,
    additional_installments: Mapping[date, Decimal] | None = None,
) -> ForecastResult:
    if not 1 <= month_count <= 120:
        raise ValidationError("预测月数必须在 1 到 120 之间。")
    additions: dict[date, Decimal] = {}
    for month, amount in (additional_installments or {}).items():
        _validate_money(amount, allow_zero=True)
        month = _month_start(month)
        additions[month] = additions.get(month, Decimal("0.00")) + amount

    liquid_assets = ledger_selectors.liquid_assets(as_of=_as_of_end(as_of))
    reserve = budget_selectors.reserve_balance(as_of=as_of)
    regular_with_savings = liquid_assets - reserve
    regular_without_savings = liquid_assets - reserve
    final_without_savings = liquid_assets
    first_month = _month_start(as_of)
    rows: list[MonthlyForecast] = []
    for offset in range(month_count):
        month = _shift_month(first_month, offset)
        snapshot = budget_selectors.monthly_snapshot(month=month)
        certain_income = budget_selectors.planned_income(
            month=month,
            reliability="CERTAIN",
            due_on_or_after=as_of if offset == 0 else None,
        )
        determined = snapshot["planned_commitment"] + additions.get(month, Decimal("0.00"))
        total_budget = snapshot["total_budget"]
        if offset == 0:
            expected_expense = max(total_budget - snapshot["actual_expense"], determined)
        else:
            expected_expense = max(total_budget, determined)
        savings_target = snapshot["savings_target"]
        budget = snapshot["budget"]
        safety_buffer = budget.minimum_safety_buffer if budget else Decimal("0.00")
        final_start = final_without_savings
        regular_end_with_savings = (
            regular_with_savings + certain_income - expected_expense - savings_target
        )
        regular_end_without_savings = regular_without_savings + certain_income - expected_expense
        final_end_without_savings = final_without_savings + certain_income - expected_expense
        status = classify_forecast_month(
            regular_end_with_savings=regular_end_with_savings,
            regular_end_without_savings=regular_end_without_savings,
            final_end_without_savings=final_end_without_savings,
            safety_buffer=safety_buffer,
        )
        rows.append(
            MonthlyForecast(
                month=month,
                regular_start_with_savings=regular_with_savings,
                regular_start_without_savings=regular_without_savings,
                final_start=final_start,
                certain_income=certain_income,
                determined_commitment=determined,
                expected_expense=expected_expense,
                savings_target=savings_target,
                safety_buffer=safety_buffer,
                regular_end_with_savings=regular_end_with_savings,
                regular_end_without_savings=regular_end_without_savings,
                final_end_without_savings=final_end_without_savings,
                status=status,
            )
        )
        regular_with_savings = regular_end_with_savings
        regular_without_savings = regular_end_without_savings
        final_without_savings = final_end_without_savings
    return ForecastResult(as_of=as_of, months=tuple(rows), overall_status=_worst_status(rows))


def installment_schedule(
    *, first_month: date, installment_count: int, installment_amount: Decimal
) -> dict[date, Decimal]:
    _validate_money(installment_amount)
    if not 1 <= installment_count <= 600:
        raise ValidationError("分期期数必须在 1 到 600 之间。")
    first_month = _month_start(first_month)
    return {
        _shift_month(first_month, offset): installment_amount for offset in range(installment_count)
    }


def preview_installment(
    *,
    as_of: date,
    month_count: int,
    first_month: date,
    installment_count: int,
    installment_amount: Decimal,
) -> InstallmentPreview:
    additions = installment_schedule(
        first_month=first_month,
        installment_count=installment_count,
        installment_amount=installment_amount,
    )
    baseline = forecast_cash_flow(as_of=as_of, month_count=month_count)
    simulated = forecast_cash_flow(
        as_of=as_of, month_count=month_count, additional_installments=additions
    )
    return InstallmentPreview(
        baseline=baseline,
        simulated=simulated,
        additional_commitments=tuple(sorted(additions.items())),
    )


def risk_alerts(*, as_of: date, forecast_months: int = 6) -> tuple[RiskAlert, ...]:
    from apps.core import selectors as core_selectors

    alerts: list[RiskAlert] = []
    budget = budget_selectors.monthly_budget(month=as_of)
    if budget is not None:
        for row in budget_selectors.category_budget_rows(budget=budget):
            if row["status"] == "OVER":
                alerts.append(
                    RiskAlert(
                        AlertLevel.DANGER,
                        "CATEGORY_OVER",
                        f"{row['category_budget'].category.name} 分类预算已达到超支阈值。",
                        row["occupancy"],
                    )
                )
            elif row["status"] == "WARNING":
                alerts.append(
                    RiskAlert(
                        AlertLevel.WARNING,
                        "CATEGORY_WARNING",
                        f"{row['category_budget'].category.name} 分类预算已达到提醒阈值。",
                        row["occupancy"],
                    )
                )

    profile = credit_selectors.active_profile()
    if profile is not None:
        for cycle in credit_selectors.unpaid_cycles(profile=profile):
            days = (cycle.due_date - as_of).days
            if days in [7, 3, 1]:
                alerts.append(
                    RiskAlert(
                        AlertLevel.WARNING,
                        "CREDIT_DUE",
                        f"信用卡账单将在 {days} 天后到期。",
                        credit_selectors.cycle_remaining_due(cycle=cycle),
                        cycle.due_date,
                    )
                )
            elif days < 0:
                alerts.append(
                    RiskAlert(
                        AlertLevel.DANGER,
                        "CREDIT_OVERDUE",
                        "信用卡账单已逾期。",
                        credit_selectors.cycle_remaining_due(cycle=cycle),
                        cycle.due_date,
                    )
                )
        capacity = repayment_capacity(as_of=as_of)
        if capacity.status != CreditRiskStatus.SAFE:
            alerts.append(
                RiskAlert(
                    AlertLevel.DANGER
                    if capacity.status == CreditRiskStatus.DANGER
                    else AlertLevel.WARNING,
                    "CREDIT_CAPACITY",
                    "信用卡常规可还款资金不足。",
                    capacity.required_due,
                    capacity.due_date,
                )
            )
        monthly_card_spend = credit_selectors.monthly_purchase_amount(profile=profile, month=as_of)
        if (
            profile.personal_monthly_limit > 0
            and monthly_card_spend >= profile.personal_monthly_limit
        ):
            alerts.append(
                RiskAlert(
                    AlertLevel.WARNING,
                    "CREDIT_PERSONAL_LIMIT",
                    "本月信用卡消费已达到个人设定上限。",
                    monthly_card_spend,
                )
            )

    forecast = forecast_cash_flow(as_of=as_of, month_count=forecast_months)
    if forecast.overall_status != ForecastRiskStatus.SAFE:
        alerts.append(
            RiskAlert(
                AlertLevel.DANGER
                if forecast.overall_status == ForecastRiskStatus.HIGH_RISK
                else AlertLevel.WARNING,
                "FORECAST_RISK",
                "未来月份的资金或安全余量存在压力。",
            )
        )

    large_threshold = core_selectors.large_expense_threshold()
    for ledger_transaction in ledger_selectors.large_flexible_expenses(
        month=as_of, threshold=large_threshold
    ):
        expense_name = ledger_transaction.counterparty or ledger_transaction.category.name
        alerts.append(
            RiskAlert(
                AlertLevel.WARNING,
                "LARGE_FLEXIBLE_EXPENSE",
                f"弹性消费“{expense_name}”达到大额阈值。",
                ledger_transaction.amount,
                timezone.localdate(ledger_transaction.occurred_at),
            )
        )

    level_order = {AlertLevel.DANGER: 0, AlertLevel.WARNING: 1, AlertLevel.INFO: 2}
    return tuple(
        sorted(
            alerts,
            key=lambda alert: (
                level_order[alert.level],
                alert.code,
                alert.due_date or date.max,
                alert.message,
            ),
        )
    )
