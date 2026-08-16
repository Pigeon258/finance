from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse

from apps.accounts.models import Account
from apps.budgets import selectors, services
from apps.budgets.models import (
    CategoryBudget,
    MonthlyBudget,
    PlannedCashFlow,
    PlannedCashFlowOccurrence,
    ReserveMovement,
)
from apps.core.models import SystemPreference
from apps.installments import services as installment_services
from apps.installments.models import InstallmentPlan
from apps.ledger import selectors as ledger_selectors
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
def expense_category():
    return Category.objects.get(name="固定订阅")


@pytest.fixture
def income_category():
    return Category.objects.get(name="其他收入")


def _occurred(year, month, day):
    return datetime(year, month, day, 12, 0, tzinfo=TZ)


def _expense_plan(*, category, bank, amount=Decimal("500.00"), **overrides):
    data = {
        "name": "云服务器",
        "direction": PlannedCashFlow.Direction.EXPENSE,
        "amount": amount,
        "category": category,
        "default_account": bank,
        "reliability": PlannedCashFlow.Reliability.CERTAIN,
        "recurrence_type": PlannedCashFlow.RecurrenceType.ONE_TIME,
        "start_date": date(2026, 7, 15),
    }
    data.update(overrides)
    return services.create_planned_cash_flow(**data)


@pytest.mark.django_db
def test_month_budget_unique_and_copy_is_idempotent(expense_category):
    source = services.save_monthly_budget(
        month=date(2026, 6, 18),
        total_expense_budget=Decimal("1000.01"),
        savings_target=Decimal("100.00"),
        minimum_safety_buffer=Decimal("50.00"),
    )
    services.save_category_budget(
        monthly_budget=source,
        category=expense_category,
        budget_amount=Decimal("300.03"),
        warning_threshold=Decimal("80.00"),
    )
    first = services.copy_monthly_budget(
        source_month=date(2026, 6, 1), target_month=date(2026, 7, 20)
    )
    second = services.copy_monthly_budget(
        source_month=date(2026, 6, 1), target_month=date(2026, 7, 1)
    )

    assert first.pk == second.pk
    assert first.month == date(2026, 7, 1)
    assert first.total_expense_budget == Decimal("300.03")
    assert CategoryBudget.objects.filter(monthly_budget=first).count() == 1
    with pytest.raises(IntegrityError), transaction.atomic():
        MonthlyBudget.objects.create(month=date(2026, 7, 1), total_expense_budget=Decimal("1.00"))


@pytest.mark.django_db
def test_category_warning_boundaries_use_decimal(expense_category, bank):
    budget = services.save_monthly_budget(
        month=date(2026, 7, 1), total_expense_budget=Decimal("1000.00")
    )
    category_budget = services.save_category_budget(
        monthly_budget=budget,
        category=expense_category,
        budget_amount=Decimal("100.00"),
        warning_threshold=Decimal("80.00"),
    )
    ledger_services.create_expense(
        account=bank,
        category=expense_category,
        amount=Decimal("80.00"),
        occurred_at=_occurred(2026, 7, 1),
        channel=Transaction.Channel.OTHER,
    )
    assert selectors.category_budget_status(category_budget=category_budget)["status"] == "WARNING"
    preference = SystemPreference.objects.get()
    preference.category_over_budget_threshold = Decimal("90.00")
    preference.save(update_fields=["category_over_budget_threshold", "updated_at"])
    ledger_services.create_expense(
        account=bank,
        category=expense_category,
        amount=Decimal("10.00"),
        occurred_at=_occurred(2026, 7, 2),
        channel=Transaction.Channel.OTHER,
    )
    status = selectors.category_budget_status(category_budget=category_budget)
    assert status["status"] == "OVER"
    assert status["usage_percentage"] == Decimal("90.00")


@pytest.mark.django_db
def test_reserve_is_virtual_and_cannot_become_negative(bank):
    before = ledger_selectors.account_balance(account=bank)
    services.record_reserve_movement(
        movement_type=ReserveMovement.MovementType.CONTRIBUTION,
        amount=Decimal("300.00"),
        occurred_on=date(2026, 7, 1),
    )
    services.record_reserve_movement(
        movement_type=ReserveMovement.MovementType.CORRECTION,
        amount=Decimal("-20.00"),
        occurred_on=date(2026, 7, 2),
    )
    services.record_reserve_movement(
        movement_type=ReserveMovement.MovementType.WITHDRAWAL,
        amount=Decimal("80.00"),
        occurred_on=date(2026, 7, 3),
    )

    assert selectors.reserve_balance() == Decimal("200.00")
    assert Transaction.objects.count() == 0
    assert ledger_selectors.account_balance(account=bank) == before
    with pytest.raises(ValidationError):
        services.record_reserve_movement(
            movement_type=ReserveMovement.MovementType.WITHDRAWAL,
            amount=Decimal("201.00"),
            occurred_on=date(2026, 7, 4),
        )


@pytest.mark.django_db
def test_monthly_occurrence_generation_clamps_and_is_idempotent(expense_category, bank):
    plan = _expense_plan(
        category=expense_category,
        bank=bank,
        recurrence_type=PlannedCashFlow.RecurrenceType.MONTHLY,
        start_date=date(2026, 1, 31),
        day_of_month=31,
        end_date=date(2026, 3, 31),
    )
    assert list(plan.occurrences.values_list("due_date", flat=True)) == [
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 31),
    ]
    assert services.generate_occurrences(plan=plan, through_date=date(2026, 12, 31)) == []
    assert plan.occurrences.count() == 3


@pytest.mark.django_db
def test_yearly_generation_handles_leap_day_and_skip_removes_commitment(expense_category, bank):
    plan = _expense_plan(
        category=expense_category,
        bank=bank,
        recurrence_type=PlannedCashFlow.RecurrenceType.YEARLY,
        start_date=date(2024, 2, 29),
        day_of_month=29,
    )
    services.generate_occurrences(plan=plan, through_date=date(2028, 12, 31))
    assert list(plan.occurrences.values_list("due_date", flat=True)) == [
        date(2024, 2, 29),
        date(2025, 2, 28),
        date(2026, 2, 28),
        date(2027, 2, 28),
        date(2028, 2, 29),
    ]
    occurrence = plan.occurrences.get(due_date=date(2026, 2, 28))
    assert selectors.planned_expense_commitment(month=date(2026, 2, 1)) == Decimal("500.00")
    services.set_occurrence_status(
        occurrence=occurrence, status=PlannedCashFlowOccurrence.Status.SKIPPED
    )
    assert selectors.planned_expense_commitment(month=date(2026, 2, 1)) == Decimal("0.00")
    assert Transaction.objects.count() == 0


@pytest.mark.django_db
def test_fixed_expense_confirmation_keeps_total_occupancy_constant(expense_category, bank):
    services.save_monthly_budget(
        month=date(2026, 7, 1),
        total_expense_budget=Decimal("1000.00"),
        savings_target=Decimal("100.00"),
    )
    plan = _expense_plan(category=expense_category, bank=bank)
    occurrence = plan.occurrences.get()
    before_balance = ledger_selectors.account_balance(account=bank)
    before = selectors.monthly_snapshot(month=date(2026, 7, 1))
    assert before["fixed_planned"] == Decimal("500.00")
    assert before["total_occupancy"] == Decimal("500.00")
    assert ledger_selectors.account_balance(account=bank) == before_balance

    services.confirm_occurrence(
        occurrence=occurrence,
        account=bank,
        actual_amount=Decimal("500.00"),
        occurred_at=_occurred(2026, 7, 20),
    )
    after = selectors.monthly_snapshot(month=date(2026, 7, 1))
    assert after["fixed_planned"] == Decimal("0.00")
    assert after["fixed_actual"] == Decimal("500.00")
    assert after["total_occupancy"] == before["total_occupancy"]
    assert ledger_selectors.account_balance(account=bank) == before_balance - Decimal("500.00")
    with pytest.raises(ValidationError):
        services.confirm_occurrence(
            occurrence=occurrence,
            account=bank,
            actual_amount=Decimal("500.00"),
            occurred_at=_occurred(2026, 7, 20),
        )


@pytest.mark.django_db
def test_budget_aggregates_ordinary_fixed_installment_refund_and_savings(expense_category, bank):
    services.save_monthly_budget(
        month=date(2026, 7, 1),
        total_expense_budget=Decimal("1200.00"),
        savings_target=Decimal("100.00"),
    )
    ledger_services.create_expense(
        account=bank,
        category=expense_category,
        amount=Decimal("100.00"),
        occurred_at=_occurred(2026, 7, 2),
        channel=Transaction.Channel.OTHER,
    )
    _expense_plan(category=expense_category, bank=bank, amount=Decimal("300.00"))
    installment_services.create_plan(
        product_name="年度服务",
        purchase_date=date(2026, 6, 1),
        original_price=Decimal("200.00"),
        category=expense_category,
        source_type=InstallmentPlan.SourceType.PLATFORM,
        installment_count=1,
        default_installment_amount=Decimal("200.00"),
        first_due_date=date(2026, 7, 31),
    )

    snapshot = selectors.monthly_snapshot(month=date(2026, 7, 1))
    assert snapshot["ordinary_expense"] == Decimal("100.00")
    assert snapshot["fixed_expense"] == Decimal("300.00")
    assert snapshot["installment"] == Decimal("200.00")
    assert snapshot["savings_target"] == Decimal("100.00")
    assert snapshot["total_occupancy"] == Decimal("600.00")
    assert snapshot["remaining"] == Decimal("600.00")


@pytest.mark.django_db
def test_confirmed_fixed_refund_reduces_original_budget_month(expense_category, bank):
    plan = _expense_plan(category=expense_category, bank=bank)
    occurrence = services.confirm_occurrence(
        occurrence=plan.occurrences.get(),
        account=bank,
        actual_amount=Decimal("500.00"),
        occurred_at=_occurred(2026, 8, 1),
    )
    ledger_services.create_refund(
        original_transaction=occurrence.linked_transaction,
        amount=Decimal("100.00"),
        occurred_at=_occurred(2026, 8, 5),
        account=bank,
    )
    breakdown = selectors.monthly_breakdown(month=date(2026, 7, 1))
    assert breakdown["fixed_actual"] == Decimal("400.00")
    assert breakdown["actual_expense"] == Decimal("400.00")


@pytest.mark.django_db
def test_expected_income_does_not_change_balance_until_confirmation(income_category, bank):
    before = ledger_selectors.account_balance(account=bank)
    plan = services.create_planned_cash_flow(
        name="生活费",
        direction=PlannedCashFlow.Direction.INCOME,
        amount=Decimal("1000.00"),
        category=income_category,
        default_account=bank,
        reliability=PlannedCashFlow.Reliability.CERTAIN,
        recurrence_type=PlannedCashFlow.RecurrenceType.ONE_TIME,
        start_date=date(2026, 7, 10),
    )
    assert selectors.planned_income(
        month=date(2026, 7, 1), reliability=PlannedCashFlow.Reliability.CERTAIN
    ) == Decimal("1000.00")
    assert ledger_selectors.account_balance(account=bank) == before

    services.confirm_occurrence(
        occurrence=plan.occurrences.get(),
        account=bank,
        actual_amount=Decimal("1000.00"),
        occurred_at=_occurred(2026, 7, 10),
    )
    assert selectors.planned_income(month=date(2026, 7, 1)) == Decimal("0.00")
    assert ledger_selectors.account_balance(account=bank) == before + Decimal("1000.00")


@pytest.mark.django_db
def test_budget_items_can_be_created_edited_and_deleted_and_total_is_automatic(
    authenticated_client, expense_category
):
    second_category = Category.objects.filter(
        category_type=Category.CategoryType.EXPENSE, is_active=True
    ).exclude(pk=expense_category.pk).first()

    page = authenticated_client.get(
        reverse("budgets:budget-item-index"), {"month": "2026-07"}
    )
    assert page.status_code == 200

    response = authenticated_client.post(
        reverse("budgets:budget-item-create") + "?month=2026-07",
        {
            "name": "工作日午餐",
            "category": expense_category.id,
            "budget_amount": "300.00",
            "warning_threshold": "80.00",
            "sort_order": "1",
        },
    )
    assert response.status_code == 302
    first_item = CategoryBudget.objects.get(name="工作日午餐")
    budget = first_item.monthly_budget
    assert budget.total_expense_budget == Decimal("300.00")

    response = authenticated_client.post(
        reverse("budgets:budget-item-create") + "?month=2026-07",
        {
            "name": "周末采购",
            "category": second_category.id,
            "budget_amount": "150.00",
            "warning_threshold": "70.00",
            "sort_order": "2",
        },
    )
    assert response.status_code == 302
    budget.refresh_from_db()
    assert budget.total_expense_budget == Decimal("450.00")

    response = authenticated_client.post(
        reverse("budgets:budget-item-edit", args=[first_item.id]),
        {
            "name": "工作日午餐",
            "category": expense_category.id,
            "budget_amount": "350.00",
            "warning_threshold": "80.00",
            "sort_order": "1",
        },
    )
    assert response.status_code == 302
    budget.refresh_from_db()
    assert budget.total_expense_budget == Decimal("500.00")

    response = authenticated_client.post(
        reverse("budgets:budget-item-delete", args=[first_item.id])
    )
    assert response.status_code == 302
    budget.refresh_from_db()
    assert budget.total_expense_budget == Decimal("150.00")

    index_page = authenticated_client.get(
        reverse("budgets:index"), {"month": "2026-07"}
    ).content.decode()
    assert "设置预算项目" in index_page
    assert "周末采购" in index_page
    assert "<td>工作日午餐</td>" not in index_page


@pytest.mark.django_db
def test_budget_index_prompts_for_budget_items_when_none_exist(authenticated_client):
    page = authenticated_client.get(reverse("budgets:index")).content.decode()

    assert "设置预算项目" in page
    assert "尚无预算项目" in page


@pytest.mark.django_db
def test_cash_flow_create_filters_categories_by_direction(
    authenticated_client, expense_category, income_category
):
    expense_page = authenticated_client.get(
        reverse("budgets:cash-flow-create"), {"direction": "EXPENSE"}
    ).content.decode()
    income_page = authenticated_client.get(
        reverse("budgets:cash-flow-create"), {"direction": "INCOME"}
    ).content.decode()

    assert expense_category.name in expense_page
    assert income_category.name not in expense_page
    assert income_category.name in income_page
    assert expense_category.name not in income_page


@pytest.mark.django_db
def test_cash_flow_income_page_creates_income_plan(
    authenticated_client, income_category, bank
):
    create_page = authenticated_client.get(
        reverse("budgets:cash-flow-create"), {"direction": "INCOME"}
    )
    token = create_page.context["submission_token"]
    response = authenticated_client.post(
        reverse("budgets:cash-flow-create"),
        {
            "submission_token": token,
            "direction": PlannedCashFlow.Direction.INCOME,
            "name": "预计工资",
            "amount": "500.00",
            "category": income_category.id,
            "default_account": bank.id,
            "reliability": PlannedCashFlow.Reliability.CERTAIN,
            "recurrence_type": PlannedCashFlow.RecurrenceType.MONTHLY,
            "start_date": "2026-07-31",
            "day_of_month": "31",
            "is_active": "on",
            "note": "",
        },
    )

    assert response.status_code == 302
    plan = PlannedCashFlow.objects.get(name="预计工资")
    assert plan.direction == PlannedCashFlow.Direction.INCOME
    assert plan.category == income_category


@pytest.mark.django_db
def test_budget_pages_and_cash_flow_duplicate_submission(
    authenticated_client, expense_category, bank
):
    assert authenticated_client.get(reverse("budgets:index")).status_code == 200
    create_page = authenticated_client.get(reverse("budgets:cash-flow-create"))
    token = create_page.context["submission_token"]
    data = {
        "submission_token": token,
        "name": "会员订阅",
        "direction": PlannedCashFlow.Direction.EXPENSE,
        "amount": "20.00",
        "category": expense_category.id,
        "default_account": bank.id,
        "reliability": PlannedCashFlow.Reliability.CERTAIN,
        "recurrence_type": PlannedCashFlow.RecurrenceType.MONTHLY,
        "start_date": "2026-07-31",
        "day_of_month": "31",
        "is_active": "on",
        "note": "",
    }
    response = authenticated_client.post(reverse("budgets:cash-flow-create"), data)
    assert response.status_code == 302
    assert PlannedCashFlow.objects.filter(name="会员订阅").count() == 1
    response = authenticated_client.post(reverse("budgets:cash-flow-create"), data)
    assert response.status_code == 302
    assert PlannedCashFlow.objects.filter(name="会员订阅").count() == 1


@pytest.mark.django_db
def test_ensure_active_plan_occurrences_starts_at_current_month(expense_category, bank):
    plan = services.create_planned_cash_flow(
        name="百度网盘会员",
        direction=PlannedCashFlow.Direction.EXPENSE,
        amount=Decimal("25.00"),
        category=expense_category,
        default_account=bank,
        reliability=PlannedCashFlow.Reliability.CERTAIN,
        recurrence_type=PlannedCashFlow.RecurrenceType.MONTHLY,
        start_date=date(2025, 7, 26),
        end_date=date(3000, 1, 1),
    )

    services.ensure_active_plan_occurrences(as_of=date(2026, 8, 16))
    future = list(
        selectors.occurrence_list(
            date_from=date(2026, 8, 1)
        ).filter(plan=plan)
    )
    assert future
    assert future[0].due_date >= date(2026, 8, 1)
    assert all(item.due_date >= date(2026, 8, 1) for item in future)


@pytest.mark.django_db
def test_category_budget_exactly_one_hundred_with_threshold_one_hundred_is_ok(
    authenticated_client, expense_category, bank
):
    budget = services.save_monthly_budget(month=date(2026, 7, 1))
    services.create_budget_item(
        month=date(2026, 7, 1),
        name="学费",
        category=expense_category,
        budget_amount=Decimal("5000.00"),
        warning_threshold=Decimal("100.00"),
    )
    ledger_services.create_expense(
        account=bank,
        category=expense_category,
        amount=Decimal("5000.00"),
        occurred_at=datetime(2026, 7, 10, 12, 0, tzinfo=TZ),
        channel=Transaction.Channel.BANK,
        item_name="学费",
    )
    rows = selectors.category_budget_rows(budget=budget)
    assert rows[0]["status"] == "OK"
    assert rows[0]["usage_percentage"] == Decimal("100.00")

    ledger_services.create_expense(
        account=bank,
        category=expense_category,
        amount=Decimal("1.00"),
        occurred_at=datetime(2026, 7, 11, 12, 0, tzinfo=TZ),
        channel=Transaction.Channel.BANK,
        item_name="学费补缴",
    )
    rows = selectors.category_budget_rows(budget=budget)
    assert rows[0]["status"] == "OVER"


@pytest.mark.django_db
def test_cash_flow_index_keeps_history_but_shows_future_occurrences(
    authenticated_client, expense_category, bank
):
    services.create_planned_cash_flow(
        name="旧周期计划",
        direction=PlannedCashFlow.Direction.EXPENSE,
        amount=Decimal("30.00"),
        category=expense_category,
        default_account=bank,
        reliability=PlannedCashFlow.Reliability.CERTAIN,
        recurrence_type=PlannedCashFlow.RecurrenceType.MONTHLY,
        start_date=date(2025, 7, 26),
        end_date=date(3000, 1, 1),
    )
    response = authenticated_client.get(reverse("budgets:cash-flow-index"))
    assert response.status_code == 200
    occurrences = response.context["occurrences"]
    assert list(occurrences) == list(selectors.occurrence_list(date_from=date(2026, 8, 1)))



@pytest.mark.django_db
def test_savings_carryover_confirms_actual_amount_and_prevents_duplicate(
    authenticated_client,
):
    previous = services.save_monthly_budget(
        month=date(2026, 6, 1), savings_target=Decimal("100.00")
    )

    page = authenticated_client.get(
        reverse("budgets:index"), {"month": "2026-07"}
    )
    assert page.context["carryover_budget"] == previous
    assert "100.00" in page.content.decode()

    response = authenticated_client.post(
        reverse("budgets:savings-carryover", args=[previous.id]),
        {"actual_amount": "80.00", "occurred_on": "2026-06-30"},
    )
    assert response.status_code == 302
    previous.refresh_from_db()
    assert previous.savings_settled_amount == Decimal("80.00")
    assert previous.savings_settled_at is not None
    movement = ReserveMovement.objects.get()
    assert movement.movement_type == ReserveMovement.MovementType.CONTRIBUTION
    assert movement.amount == Decimal("80.00")
    assert movement.occurred_on == date(2026, 6, 30)

    duplicate = authenticated_client.post(
        reverse("budgets:savings-carryover", args=[previous.id]),
        {"actual_amount": "80.00", "occurred_on": "2026-06-30"},
    )
    assert duplicate.status_code == 302
    assert ReserveMovement.objects.count() == 1
