from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Account
from apps.budgets import services as budget_services
from apps.credit import services as credit_services
from apps.credit.models import BillingCycle
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
def expense_category():
    return Category.objects.get(name="餐饮")


def _occurred(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 12, 0, tzinfo=TZ)


@pytest.mark.django_db
def test_budget_page_uses_localized_status_and_keeps_zero_usage_visible(
    authenticated_client, expense_category
):
    budget = budget_services.save_monthly_budget(
        month=date(2026, 7, 1),
        total_expense_budget=Decimal("800.00"),
        savings_target=Decimal("100.00"),
    )
    budget_services.save_category_budget(
        monthly_budget=budget,
        category=expense_category,
        budget_amount=Decimal("100.00"),
    )

    response = authenticated_client.get(reverse("budgets:index"), {"month": "2026-07"})
    content = response.content.decode()

    assert "0.00%" in content
    assert "正常" in content
    assert "<td>OVER</td>" not in content

    ledger_services.create_expense(
        account=Account.objects.get(account_type=Account.AccountType.BANK),
        category=expense_category,
        amount=Decimal("120.00"),
        occurred_at=_occurred(2026, 7, 3),
        channel=Transaction.Channel.BANK,
    )

    response = authenticated_client.get(reverse("budgets:index"), {"month": "2026-07"})
    content = response.content.decode()

    assert "超支" in content
    assert "<td>OVER</td>" not in content
    assert "<td>WARNING</td>" not in content


@pytest.mark.django_db
def test_credit_pages_use_effective_chinese_status(authenticated_client, expense_category):
    card = Account.objects.get(account_type=Account.AccountType.CREDIT_CARD)
    profile = credit_services.save_profile(
        account=card,
        credit_limit=Decimal("5000.00"),
        personal_monthly_limit=Decimal("1000.00"),
        statement_day=15,
        due_day=5,
    )
    credit_services.create_credit_card_purchase(
        profile=profile,
        account=card,
        category=expense_category,
        amount=Decimal("100.00"),
        occurred_at=_occurred(2026, 6, 10),
        channel=Transaction.Channel.OTHER,
    )
    cycle = profile.billing_cycles.get()
    cycle.official_statement_amount = Decimal("100.00")
    cycle.official_due_amount = Decimal("100.00")
    cycle.due_date = date(2026, 8, 5)
    cycle.status = BillingCycle.Status.ISSUED
    cycle.issued_at = timezone.now()
    cycle.save(update_fields=[
        "official_statement_amount",
        "official_due_amount",
        "due_date",
        "status",
        "issued_at",
        "updated_at",
    ])

    with patch("apps.credit.views.timezone.localdate", return_value=date(2026, 8, 20)):
        overview = authenticated_client.get(reverse("credit:overview")).content.decode()
        detail = authenticated_client.get(
            reverse("credit:cycle-detail", args=[cycle.id])
        ).content.decode()

    assert "已逾期" in overview
    assert "<td>OVERDUE</td>" not in overview
    assert "危险" in overview

    assert "已逾期" in detail
    assert "待还款" not in detail


@pytest.mark.django_db
def test_risk_and_preview_pages_use_chinese_status_labels(
    authenticated_client, expense_category
):
    bank = Account.objects.get(account_type=Account.AccountType.BANK)
    budget = budget_services.save_monthly_budget(
        month=date(2026, 7, 1),
        total_expense_budget=Decimal("800.00"),
    )
    budget_services.save_category_budget(
        monthly_budget=budget,
        category=expense_category,
        budget_amount=Decimal("100.00"),
    )
    ledger_services.create_expense(
        account=bank,
        category=expense_category,
        amount=Decimal("120.00"),
        occurred_at=_occurred(2026, 7, 3),
        channel=Transaction.Channel.BANK,
    )

    risk = authenticated_client.get(
        reverse("analytics:risk-overview"),
        {"as_of": "2026-07-15", "month_count": "3"},
    ).content.decode()
    preview = authenticated_client.post(
        reverse("analytics:installment-preview"),
        {
            "as_of": "2026-07-15",
            "month_count": "3",
            "first_month": "2026-08",
            "installment_count": "3",
            "installment_amount": "50",
        },
    ).content.decode()

    assert "危险" in risk
    assert "<strong>DANGER</strong>" not in risk
    assert "<td>HIGH_RISK</td>" not in risk

    assert "高风险" in preview
    assert "<td>HIGH_RISK</td>" not in preview


def test_status_badge_has_safe_default_component_styles(settings):
    app_css = (settings.BASE_DIR / "static" / "css" / "app.css").read_text(encoding="utf-8")

    assert '[data-pf-part="status-badge"]' in app_css
    assert '[data-pf-part="status-badge"][data-status="success"]' in app_css
    assert '[data-pf-part="status-badge"][data-status="warning"]' in app_css
    assert '[data-pf-part="status-badge"][data-status="danger"]' in app_css
    assert '[data-pf-part="status-badge"][data-status="neutral"]' in app_css


@pytest.mark.django_db
def test_secondary_pages_share_form_and_table_components(authenticated_client):
    import_rule_page = authenticated_client.get(
        reverse("imports:category-rule-create")
    ).content.decode()
    upcoming_page = authenticated_client.get(reverse("analytics:upcoming")).content.decode()

    assert 'data-pf-part="form-panel"' in import_rule_page
    assert "<p><label" not in import_rule_page
    assert '<table data-pf-part="data-table">' in upcoming_page
