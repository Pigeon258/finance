from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import Account
from apps.analytics import selectors
from apps.budgets import selectors as budget_selectors
from apps.budgets import services as budget_services
from apps.budgets.models import PlannedCashFlow
from apps.credit import services as credit_services
from apps.installments import services as installment_services
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
def wechat():
    return Account.objects.get(account_type=Account.AccountType.WECHAT)


@pytest.fixture
def card():
    return Account.objects.get(account_type=Account.AccountType.CREDIT_CARD)


@pytest.fixture
def income_category():
    return Category.objects.get(name="其他收入")


@pytest.fixture
def expense_category():
    return Category.objects.get(name="餐饮")


def _occurred(year, month, day, hour=12, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=TZ)


def _profile(card):
    return credit_services.save_profile(
        account=card,
        credit_limit=Decimal("5000.00"),
        personal_monthly_limit=Decimal("1000.00"),
        statement_day=15,
        due_day=5,
    )


def _ledger_fixture(bank, wechat, card, income_category, expense_category):
    ledger_services.create_income(
        account=bank,
        category=income_category,
        amount=Decimal("500.00"),
        occurred_at=_occurred(2026, 7, 2),
        channel=Transaction.Channel.BANK,
    )
    expense = ledger_services.create_expense(
        account=bank,
        category=expense_category,
        amount=Decimal("200.00"),
        occurred_at=_occurred(2026, 7, 3),
        channel=Transaction.Channel.BANK,
    )
    ledger_services.create_refund(
        original_transaction=expense,
        amount=Decimal("50.00"),
        occurred_at=_occurred(2026, 7, 4),
    )
    ledger_services.create_transfer(
        source_account=bank,
        destination_account=wechat,
        amount=Decimal("100.00"),
        occurred_at=_occurred(2026, 7, 5),
    )
    ledger_services.create_balance_adjustment(
        account=bank,
        balance_delta=Decimal("30.00"),
        occurred_at=_occurred(2026, 7, 6),
        reason="测试核对",
    )
    ledger_services.create_credit_card_purchase(
        account=card,
        category=expense_category,
        amount=Decimal("300.00"),
        occurred_at=_occurred(2026, 7, 7),
        channel=Transaction.Channel.OTHER,
    )


@pytest.mark.django_db
def test_dashboard_reconciles_core_metrics_and_excludes_non_statistical_transactions(
    bank, wechat, card, income_category, expense_category
):
    _profile(card)
    _ledger_fixture(bank, wechat, card, income_category, expense_category)
    budget_services.save_monthly_budget(
        month=date(2026, 7, 1),
        total_expense_budget=Decimal("1000.00"),
        savings_target=Decimal("100.00"),
    )

    snapshot = selectors.dashboard_snapshot(month=date(2026, 7, 1), as_of=date(2026, 7, 10))
    assert snapshot.liquid_assets == Decimal("1380.00")
    assert snapshot.credit_liability == Decimal("300.00")
    assert snapshot.net_funds == Decimal("1080.00")
    assert snapshot.allocatable_funds == Decimal("430.00")
    assert snapshot.monthly_income == Decimal("500.00")
    assert snapshot.budget["actual_expense"] == Decimal("450.00")
    assert snapshot.budget["allocatable_remaining"] == Decimal("550.00")
    assert snapshot.budget["total_occupancy"] == Decimal("450.00")
    assert snapshot.budget["remaining"] == Decimal("550.00")


@pytest.mark.django_db
def test_dashboard_budget_status_treats_exact_100_as_ok(
    bank, expense_category
):
    budget_services.save_monthly_budget(month=date(2026, 7, 1))
    budget_services.create_budget_item(
        month=date(2026, 7, 1),
        name="学费",
        category=expense_category,
        budget_amount=Decimal("100.00"),
        warning_threshold=Decimal("100.00"),
    )
    ledger_services.create_expense(
        account=bank,
        category=expense_category,
        amount=Decimal("100.00"),
        occurred_at=_occurred(2026, 7, 10),
        channel=Transaction.Channel.BANK,
        item_name="学费",
    )

    snapshot = selectors.dashboard_snapshot(month=date(2026, 7, 1), as_of=date(2026, 7, 11))
    assert snapshot.budget_status == "OK"
    assert snapshot.budget_status_label == "正常"

    ledger_services.create_expense(
        account=bank,
        category=expense_category,
        amount=Decimal("0.01"),
        occurred_at=_occurred(2026, 7, 11),
        channel=Transaction.Channel.BANK,
        item_name="学费补缴",
    )
    snapshot = selectors.dashboard_snapshot(month=date(2026, 7, 1), as_of=date(2026, 7, 12))
    assert snapshot.budget_status == "OVER"
    assert snapshot.budget_status_label == "超过预算"


@pytest.mark.django_db
def test_reports_use_decimal_refund_netting_and_exclude_transfer_adjustment(
    bank, wechat, card, income_category, expense_category
):
    _profile(card)
    _ledger_fixture(bank, wechat, card, income_category, expense_category)
    report = selectors.report_snapshot(date_from=date(2026, 7, 1), date_to=date(2026, 7, 31))

    assert report.transaction_count == 6
    assert report.monthly[0].income == Decimal("500.00")
    assert report.monthly[0].expense == Decimal("450.00")
    assert report.monthly[0].surplus == Decimal("50.00")
    assert report.categories[0].amount == Decimal("450.00")
    assert sum((row.amount for row in report.daily), Decimal("0.00")) == Decimal("450.00")
    assert report.net_funds[-1].amount == Decimal("1080.00")


@pytest.mark.django_db
def test_reports_respect_timezone_day_boundary(bank, income_category):
    ledger_services.create_income(
        account=bank,
        category=income_category,
        amount=Decimal("10.00"),
        occurred_at=_occurred(2026, 7, 31, 23, 30),
        channel=Transaction.Channel.BANK,
    )
    ledger_services.create_income(
        account=bank,
        category=income_category,
        amount=Decimal("20.00"),
        occurred_at=_occurred(2026, 8, 1, 0, 30),
        channel=Transaction.Channel.BANK,
    )

    report = selectors.report_snapshot(date_from=date(2026, 7, 31), date_to=date(2026, 7, 31))
    assert report.transaction_count == 1
    assert report.monthly[0].income == Decimal("10.00")


@pytest.mark.django_db
def test_ledger_report_filters_by_type_account_and_category(bank, wechat, expense_category):
    other_category = Category.objects.get(name="娱乐")
    ledger_services.create_expense(
        account=bank,
        category=expense_category,
        amount=Decimal("100.00"),
        occurred_at=_occurred(2026, 7, 1),
        channel=Transaction.Channel.BANK,
    )
    ledger_services.create_expense(
        account=wechat,
        category=other_category,
        amount=Decimal("50.00"),
        occurred_at=_occurred(2026, 7, 1),
        channel=Transaction.Channel.WECHAT,
    )

    report = selectors.report_snapshot(
        date_from=date(2026, 7, 1),
        date_to=date(2026, 7, 31),
        transaction_type=Transaction.TransactionType.EXPENSE,
        account_id=bank.id,
        category_id=expense_category.id,
    )
    assert report.transaction_count == 1
    assert report.monthly[0].expense == Decimal("100.00")
    assert [(row.label, row.amount) for row in report.categories] == [("餐饮", Decimal("100.00"))]


@pytest.mark.django_db
def test_reports_fill_empty_cross_year_periods():
    report = selectors.report_snapshot(date_from=date(2025, 12, 31), date_to=date(2026, 1, 2))
    assert [row.period for row in report.monthly] == [date(2025, 12, 1), date(2026, 1, 1)]
    assert all(row.income == Decimal("0.00") for row in report.monthly)
    assert len(report.daily) == 3
    assert len(report.net_funds) == 3


@pytest.mark.django_db
def test_upcoming_items_include_both_boundaries_and_exclude_day_31(
    bank, expense_category, income_category
):
    installment_services.create_plan(
        product_name="平台课程",
        purchase_date=date(2026, 6, 1),
        original_price=Decimal("200.00"),
        category=expense_category,
        source_type=InstallmentPlan.SourceType.PLATFORM,
        installment_count=2,
        default_installment_amount=Decimal("100.00"),
        first_due_date=date(2026, 7, 10),
    )
    budget_services.create_planned_cash_flow(
        name="月末固定支出",
        direction=PlannedCashFlow.Direction.EXPENSE,
        amount=Decimal("50.00"),
        category=expense_category,
        default_account=bank,
        reliability=PlannedCashFlow.Reliability.CERTAIN,
        recurrence_type=PlannedCashFlow.RecurrenceType.ONE_TIME,
        start_date=date(2026, 8, 9),
    )
    budget_services.create_planned_cash_flow(
        name="边界外收入",
        direction=PlannedCashFlow.Direction.INCOME,
        amount=Decimal("500.00"),
        category=income_category,
        default_account=bank,
        reliability=PlannedCashFlow.Reliability.CERTAIN,
        recurrence_type=PlannedCashFlow.RecurrenceType.ONE_TIME,
        start_date=date(2026, 8, 10),
    )

    items = selectors.upcoming_items(as_of=date(2026, 7, 10), alerts=())
    assert any(item.due_date == date(2026, 7, 10) for item in items)
    assert any(item.due_date == date(2026, 8, 9) for item in items)
    assert all(item.due_date != date(2026, 8, 10) for item in items)


@pytest.mark.django_db
def test_posted_credit_installment_is_informational_not_duplicate_payable(card, expense_category):
    profile = _profile(card)
    plan = installment_services.create_plan(
        product_name="手机",
        purchase_date=date(2026, 6, 1),
        original_price=Decimal("200.00"),
        category=expense_category,
        source_type=InstallmentPlan.SourceType.CREDIT_CARD,
        installment_count=1,
        default_installment_amount=Decimal("200.00"),
        first_due_month=date(2026, 7, 1),
    )
    item = installment_services.post_item(
        item=plan.items.get(),
        actual_amount=Decimal("200.00"),
        occurred_at=_occurred(2026, 6, 20),
    )
    credit_services.issue_cycle(
        cycle=item.billing_cycle,
        official_statement_amount=Decimal("200.00"),
        official_due_amount=Decimal("200.00"),
        due_date=date(2026, 7, 5),
    )

    items = selectors.upcoming_items(as_of=date(2026, 7, 1), alerts=())
    assert sum(
        (entry.amount or Decimal("0.00") for entry in items if entry.counts_as_payable),
        Decimal("0.00"),
    ) == Decimal("200.00")
    assert any("不重复累计" in entry.note for entry in items)
    assert profile.billing_cycles.count() == 1


@pytest.mark.django_db
def test_posted_installment_moves_from_planned_to_actual_once(bank, expense_category):
    plan = installment_services.create_plan(
        product_name="课程",
        purchase_date=date(2026, 7, 1),
        original_price=Decimal("100.00"),
        category=expense_category,
        source_type=InstallmentPlan.SourceType.PLATFORM,
        installment_count=1,
        default_installment_amount=Decimal("100.00"),
        first_due_date=date(2026, 7, 20),
    )
    installment_services.post_item(
        item=plan.items.get(),
        actual_amount=Decimal("90.00"),
        occurred_at=_occurred(2026, 7, 20),
        account=bank,
    )
    report = selectors.report_snapshot(date_from=date(2026, 7, 1), date_to=date(2026, 7, 31))
    assert report.installments[0].actual == Decimal("90.00")
    assert report.installments[0].planned == Decimal("0.00")
    assert report.installments[0].total == Decimal("90.00")


@pytest.mark.django_db
def test_reports_budget_installment_credit_and_savings_sections(
    bank, card, income_category, expense_category
):
    profile = _profile(card)
    ledger_services.create_income(
        account=bank,
        category=income_category,
        amount=Decimal("500.00"),
        occurred_at=_occurred(2026, 7, 2),
        channel=Transaction.Channel.BANK,
    )
    credit_services.create_credit_card_purchase(
        profile=profile,
        account=card,
        category=expense_category,
        amount=Decimal("100.00"),
        occurred_at=_occurred(2026, 7, 3),
        channel=Transaction.Channel.OTHER,
    )
    budget = budget_services.save_monthly_budget(
        month=date(2026, 7, 1),
        total_expense_budget=Decimal("800.00"),
        savings_target=Decimal("300.00"),
    )
    budget_services.save_category_budget(
        monthly_budget=budget,
        category=expense_category,
        budget_amount=Decimal("400.00"),
    )
    installment_services.create_plan(
        product_name="平台课程",
        purchase_date=date(2026, 7, 1),
        original_price=Decimal("200.00"),
        category=expense_category,
        source_type=InstallmentPlan.SourceType.PLATFORM,
        installment_count=2,
        default_installment_amount=Decimal("100.00"),
        first_due_date=date(2026, 7, 20),
    )

    report = selectors.report_snapshot(
        date_from=date(2026, 7, 1), date_to=date(2026, 8, 31), budget_month=date(2026, 7, 1)
    )
    assert report.budget_summary["total_budget"] == Decimal("400.00")
    assert report.budget_rows[0].occupied == Decimal("200.00")
    assert report.credit.configured is True
    assert report.credit.monthly_purchases[0].amount == Decimal("100.00")
    assert [row.planned for row in report.installments] == [
        Decimal("100.00"),
        Decimal("100.00"),
    ]
    assert report.savings[0].income_expense_surplus == Decimal("400.00")
    assert report.savings[0].completion_percentage == Decimal("133.33")


@pytest.mark.django_db
def test_report_query_count_is_bounded(bank, income_category):
    ledger_services.create_income(
        account=bank,
        category=income_category,
        amount=Decimal("10.00"),
        occurred_at=_occurred(2026, 1, 1),
        channel=Transaction.Channel.BANK,
    )
    with CaptureQueriesContext(connection) as queries:
        selectors.report_snapshot(date_from=date(2026, 1, 1), date_to=date(2026, 12, 31))
    assert len(queries) <= 20


@pytest.mark.django_db
def test_category_budget_report_queries_do_not_scale_with_category_count():
    budget = budget_services.save_monthly_budget(
        month=date(2026, 7, 1), total_expense_budget=Decimal("2000.00")
    )
    categories = list(Category.objects.filter(category_type=Category.CategoryType.EXPENSE)[:6])
    for category in categories:
        budget_services.save_category_budget(
            monthly_budget=budget,
            category=category,
            budget_amount=Decimal("100.00"),
        )
    with CaptureQueriesContext(connection) as queries:
        rows = budget_selectors.category_budget_rows(budget=budget)
    assert len(rows) == 6
    assert len(queries) <= 6


@pytest.mark.django_db
def test_dashboard_and_report_pages_are_read_only_and_use_local_charts(authenticated_client, bank):
    before = Transaction.objects.count()
    response = authenticated_client.get(reverse("core:home"))
    assert response.status_code == 200
    assert "首页仪表盘" in response.content.decode()
    response = authenticated_client.get(reverse("analytics:dashboard"), {"month": "2026-07"})
    assert response.status_code == 200
    dashboard_content = response.content.decode()
    assert 'data-theme-id="aurora-ledger"' in dashboard_content
    assert "/static/themes/aurora-ledger/theme.css?v=" in dashboard_content
    assert dashboard_content.count('data-pf-part="status-badge" data-status=') == 3
    response = authenticated_client.get(
        reverse("analytics:reports"),
        {
            "date_from": "2026-07-01",
            "date_to": "2026-07-31",
            "budget_month": "2026-07",
        },
    )
    assert response.status_code == 200
    assert response.context["report"] is not None
    content = response.content.decode()
    assert "/static/vendor/echarts/echarts.min.js" in content
    assert 'id="report-chart-theme"' in content
    assert "#65e6cf" in content
    assert "cdn.jsdelivr.net" not in content
    assert Transaction.objects.count() == before


@pytest.mark.django_db
def test_report_form_rejects_reversed_and_overlong_ranges(authenticated_client):
    response = authenticated_client.get(
        reverse("analytics:reports"),
        {
            "date_from": "2026-08-01",
            "date_to": "2026-07-01",
            "budget_month": "2026-07",
        },
    )
    assert response.context["report"] is None
    assert "开始日期不得晚于结束日期" in response.content.decode()

    response = authenticated_client.get(
        reverse("analytics:reports"),
        {
            "date_from": "2025-01-01",
            "date_to": "2026-12-31",
            "budget_month": "2026-07",
        },
    )
    assert response.context["report"] is None
    assert "单次报表日期范围不得超过 367 天" in response.content.decode()
