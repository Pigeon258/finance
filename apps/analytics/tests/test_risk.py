from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse

from apps.accounts.models import Account
from apps.analytics import services
from apps.analytics.services import CreditRiskStatus, ForecastRiskStatus
from apps.budgets import services as budget_services
from apps.budgets.models import MonthlyBudget, PlannedCashFlow, ReserveMovement
from apps.credit import services as credit_services
from apps.credit.models import BillingCycle
from apps.installments.models import InstallmentPlan
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
    account.initial_balance = Decimal("1000.00")
    account.save(update_fields=["initial_balance"])
    return account


@pytest.fixture
def card():
    return Account.objects.get(account_type=Account.AccountType.CREDIT_CARD)


@pytest.fixture
def necessary_category():
    return Category.objects.get(name="餐饮")


@pytest.fixture
def flexible_category():
    return Category.objects.get(name="娱乐")


@pytest.fixture
def income_category():
    return Category.objects.get(name="其他收入")


def _occurred(year, month, day):
    return datetime(year, month, day, 12, 0, tzinfo=TZ)


def _profile(card, personal_limit=Decimal("500.00")):
    return credit_services.save_profile(
        account=card,
        credit_limit=Decimal("10000.00"),
        personal_monthly_limit=personal_limit,
        statement_day=15,
        due_day=5,
    )


@pytest.mark.parametrize(
    ("required", "expected"),
    [
        (Decimal("500.00"), CreditRiskStatus.SAFE),
        (Decimal("500.01"), CreditRiskStatus.USE_RESERVE),
        (Decimal("700.01"), CreditRiskStatus.DANGER),
    ],
)
def test_repayment_capacity_boundaries(required, expected):
    result = services.calculate_repayment_capacity(
        as_of=date(2026, 7, 1),
        due_date=date(2026, 7, 10),
        required_due=required,
        liquid_assets=Decimal("1000.00"),
        reserve_balance=Decimal("200.00"),
        necessary_protection=Decimal("300.00"),
    )
    assert result.regular_available == Decimal("500.00")
    assert result.final_available == Decimal("700.00")
    assert result.status == expected


def test_repayment_capacity_preserves_negative_liquid_assets():
    result = services.calculate_repayment_capacity(
        as_of=date(2026, 7, 1),
        due_date=date(2026, 7, 10),
        required_due=Decimal("1.00"),
        liquid_assets=Decimal("-10.00"),
        reserve_balance=Decimal("0.00"),
        necessary_protection=Decimal("0.00"),
    )
    assert result.liquid_assets == Decimal("-10.00")
    assert result.regular_available == Decimal("0.00")
    assert result.final_available == Decimal("0.00")
    assert result.status == CreditRiskStatus.DANGER


@pytest.mark.parametrize(
    ("with_savings", "without_savings", "with_reserve", "expected"),
    [
        ("100.00", "100.00", "100.00", ForecastRiskStatus.SAFE),
        ("99.99", "100.00", "100.00", ForecastRiskStatus.AFFORDABLE),
        ("99.99", "99.99", "100.00", ForecastRiskStatus.USE_RESERVE),
        ("99.99", "99.99", "99.99", ForecastRiskStatus.HIGH_RISK),
    ],
)
def test_forecast_status_boundaries(with_savings, without_savings, with_reserve, expected):
    assert (
        services.classify_forecast_month(
            regular_end_with_savings=Decimal(with_savings),
            regular_end_without_savings=Decimal(without_savings),
            final_end_without_savings=Decimal(with_reserve),
            safety_buffer=Decimal("100.00"),
        )
        == expected
    )


@pytest.mark.django_db
def test_current_month_forecasts_only_remaining_budget(bank, necessary_category):
    budget_services.save_monthly_budget(
        month=date(2026, 7, 1), total_expense_budget=Decimal("500.00")
    )
    ledger_services.create_expense(
        account=bank,
        category=necessary_category,
        amount=Decimal("200.00"),
        occurred_at=_occurred(2026, 7, 10),
        channel=Transaction.Channel.OTHER,
    )

    result = services.forecast_cash_flow(as_of=date(2026, 7, 15), month_count=1)
    row = result.months[0]
    assert row.regular_start_without_savings == Decimal("800.00")
    assert row.expected_expense == Decimal("300.00")
    assert row.regular_end_without_savings == Decimal("500.00")


@pytest.mark.django_db
def test_only_future_certain_income_enters_forecast(bank, income_category):
    for name, amount, reliability, due_date in [
        ("已错过确定收入", "500.00", PlannedCashFlow.Reliability.CERTAIN, date(2026, 7, 1)),
        ("未来确定收入", "100.00", PlannedCashFlow.Reliability.CERTAIN, date(2026, 7, 20)),
        ("较可能收入", "1000.00", PlannedCashFlow.Reliability.LIKELY, date(2026, 7, 20)),
        ("不确定收入", "1000.00", PlannedCashFlow.Reliability.UNCERTAIN, date(2026, 7, 20)),
    ]:
        budget_services.create_planned_cash_flow(
            name=name,
            direction=PlannedCashFlow.Direction.INCOME,
            amount=Decimal(amount),
            category=income_category,
            default_account=bank,
            reliability=reliability,
            recurrence_type=PlannedCashFlow.RecurrenceType.ONE_TIME,
            start_date=due_date,
        )

    result = services.forecast_cash_flow(as_of=date(2026, 7, 15), month_count=1)
    assert result.months[0].certain_income == Decimal("100.00")


@pytest.mark.django_db
def test_forecast_rolls_scenarios_and_uses_worst_status(bank):
    budget_services.record_reserve_movement(
        movement_type=ReserveMovement.MovementType.CONTRIBUTION,
        amount=Decimal("200.00"),
        occurred_on=date(2026, 7, 1),
    )
    budget_services.save_monthly_budget(
        month=date(2026, 7, 1),
        total_expense_budget=Decimal("600.00"),
        savings_target=Decimal("200.00"),
        minimum_safety_buffer=Decimal("100.00"),
    )
    budget_services.save_monthly_budget(
        month=date(2026, 8, 1),
        total_expense_budget=Decimal("400.00"),
        minimum_safety_buffer=Decimal("100.00"),
    )

    result = services.forecast_cash_flow(as_of=date(2026, 7, 1), month_count=2)
    assert result.months[0].status == ForecastRiskStatus.AFFORDABLE
    assert result.months[1].regular_start_without_savings == Decimal("200.00")
    assert result.months[1].status == ForecastRiskStatus.HIGH_RISK
    assert result.overall_status == ForecastRiskStatus.HIGH_RISK


@pytest.mark.django_db
def test_necessary_protection_uses_larger_of_budget_and_commitment(bank, necessary_category):
    budget = budget_services.save_monthly_budget(
        month=date(2026, 7, 1), total_expense_budget=Decimal("1100.00")
    )
    budget_services.save_category_budget(
        monthly_budget=budget,
        category=necessary_category,
        budget_amount=Decimal("500.00"),
    )
    ledger_services.create_expense(
        account=bank,
        category=necessary_category,
        amount=Decimal("100.00"),
        occurred_at=_occurred(2026, 7, 2),
        channel=Transaction.Channel.OTHER,
    )
    budget_services.create_planned_cash_flow(
        name="必要固定支出",
        direction=PlannedCashFlow.Direction.EXPENSE,
        amount=Decimal("600.00"),
        category=necessary_category,
        default_account=bank,
        reliability=PlannedCashFlow.Reliability.CERTAIN,
        recurrence_type=PlannedCashFlow.RecurrenceType.ONE_TIME,
        start_date=date(2026, 7, 10),
    )

    assert services.necessary_protection_funds(
        as_of=date(2026, 7, 5), due_date=date(2026, 7, 15)
    ) == Decimal("600.00")


@pytest.mark.django_db
def test_credit_capacity_integration_can_become_danger(bank, card, necessary_category):
    profile = _profile(card)
    credit_services.create_credit_card_purchase(
        profile=profile,
        account=card,
        category=necessary_category,
        amount=Decimal("500.00"),
        occurred_at=_occurred(2026, 7, 10),
        channel=Transaction.Channel.OTHER,
    )
    cycle = BillingCycle.objects.get()
    credit_services.issue_cycle(
        cycle=cycle,
        official_statement_amount=Decimal("500.00"),
        official_due_amount=Decimal("500.00"),
        due_date=date(2026, 8, 5),
    )
    budget = budget_services.save_monthly_budget(
        month=date(2026, 7, 1), total_expense_budget=Decimal("1000.00")
    )
    budget_services.save_category_budget(
        monthly_budget=budget,
        category=necessary_category,
        budget_amount=Decimal("1100.00"),
    )

    capacity = services.repayment_capacity(as_of=date(2026, 7, 20))
    assert capacity.required_due == Decimal("500.00")
    assert capacity.necessary_protection == Decimal("600.00")
    assert capacity.final_available == Decimal("400.00")
    assert capacity.status == CreditRiskStatus.DANGER


@pytest.mark.django_db
def test_installment_preview_is_read_only_and_degrades_status(bank):
    budget_services.save_monthly_budget(
        month=date(2026, 7, 1),
        total_expense_budget=Decimal("500.00"),
        minimum_safety_buffer=Decimal("100.00"),
    )
    counts_before = {
        "transactions": Transaction.objects.count(),
        "budgets": MonthlyBudget.objects.count(),
        "plans": InstallmentPlan.objects.count(),
    }
    preview = services.preview_installment(
        as_of=date(2026, 7, 1),
        month_count=2,
        first_month=date(2026, 7, 1),
        installment_count=2,
        installment_amount=Decimal("600.00"),
    )
    assert preview.baseline.overall_status == ForecastRiskStatus.SAFE
    assert preview.simulated.overall_status == ForecastRiskStatus.HIGH_RISK
    assert preview.additional_commitments == (
        (date(2026, 7, 1), Decimal("600.00")),
        (date(2026, 8, 1), Decimal("600.00")),
    )
    assert counts_before == {
        "transactions": Transaction.objects.count(),
        "budgets": MonthlyBudget.objects.count(),
        "plans": InstallmentPlan.objects.count(),
    }


@pytest.mark.django_db
def test_internal_alerts_cover_thresholds_and_have_stable_order(bank, card, flexible_category):
    profile = _profile(card, personal_limit=Decimal("100.00"))
    credit_services.create_credit_card_purchase(
        profile=profile,
        account=card,
        category=flexible_category,
        amount=Decimal("100.00"),
        occurred_at=_occurred(2026, 7, 10),
        channel=Transaction.Channel.OTHER,
    )
    cycle = BillingCycle.objects.get()
    credit_services.issue_cycle(
        cycle=cycle,
        official_statement_amount=Decimal("100.00"),
        official_due_amount=Decimal("100.00"),
        due_date=date(2026, 8, 5),
    )
    ledger_services.create_expense(
        account=bank,
        category=flexible_category,
        amount=Decimal("600.00"),
        occurred_at=_occurred(2026, 7, 20),
        channel=Transaction.Channel.OTHER,
        counterparty="旅行用品",
    )
    budget = budget_services.save_monthly_budget(
        month=date(2026, 7, 1), total_expense_budget=Decimal("1000.00")
    )
    budget_services.save_category_budget(
        monthly_budget=budget,
        category=flexible_category,
        budget_amount=Decimal("1000.00"),
        warning_threshold=Decimal("50.00"),
    )

    alerts = services.risk_alerts(as_of=date(2026, 7, 29), forecast_months=1)
    codes = [alert.code for alert in alerts]
    assert "CATEGORY_WARNING" in codes
    assert "CREDIT_DUE" in codes
    assert "CREDIT_PERSONAL_LIMIT" in codes
    assert "LARGE_FLEXIBLE_EXPENSE" in codes
    assert alerts == services.risk_alerts(as_of=date(2026, 7, 29), forecast_months=1)


@pytest.mark.django_db
def test_large_expense_alert_requires_amount_above_threshold(bank, flexible_category):
    ledger_services.create_expense(
        account=bank,
        category=flexible_category,
        amount=Decimal("500.00"),
        occurred_at=_occurred(2026, 7, 20),
        channel=Transaction.Channel.OTHER,
    )
    alerts = services.risk_alerts(as_of=date(2026, 7, 29), forecast_months=1)
    assert "LARGE_FLEXIBLE_EXPENSE" not in [alert.code for alert in alerts]


@pytest.mark.django_db
def test_risk_pages_and_preview_do_not_write(authenticated_client, bank):
    before = Transaction.objects.count()
    response = authenticated_client.get(reverse("analytics:risk-overview"))
    assert response.status_code == 200
    assert response.context["forecast"] is not None
    response = authenticated_client.get(
        reverse("analytics:risk-overview"), {"as_of": "2026-07-01", "month_count": "3"}
    )
    assert response.status_code == 200
    response = authenticated_client.post(
        reverse("analytics:installment-preview"),
        {
            "as_of": "2026-07-01",
            "month_count": "3",
            "first_month": "2026-07",
            "installment_count": "3",
            "installment_amount": "100.00",
        },
    )
    assert response.status_code == 200
    assert response.context["preview"] is not None
    assert Transaction.objects.count() == before
