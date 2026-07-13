from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from . import selectors, services
from .forms import (
    DashboardMonthForm,
    ForecastForm,
    InstallmentPreviewForm,
    ReportFilterForm,
)


def _add_error(form, error: ValidationError) -> None:
    form.add_error(None, " ".join(error.messages))


@require_GET
def dashboard(request: HttpRequest):
    today = timezone.localdate()
    form = DashboardMonthForm(request.GET or {"month": today.strftime("%Y-%m")})
    snapshot = None
    if form.is_valid():
        snapshot = selectors.dashboard_snapshot(month=form.cleaned_data["month"], as_of=today)
    return render(request, "analytics/dashboard.html", {"form": form, "snapshot": snapshot})


@require_GET
def reports(request: HttpRequest):
    today = timezone.localdate()
    default_from = today.replace(month=1, day=1)
    form = ReportFilterForm(
        request.GET
        or {
            "date_from": default_from.isoformat(),
            "date_to": today.isoformat(),
            "budget_month": today.strftime("%Y-%m"),
        }
    )
    report = None
    chart_data = None
    if form.is_valid():
        cleaned = form.cleaned_data
        report = selectors.report_snapshot(
            date_from=cleaned["date_from"],
            date_to=cleaned["date_to"],
            transaction_type=cleaned["transaction_type"] or None,
            account_id=cleaned["account"].id if cleaned["account"] else None,
            category_id=cleaned["category"].id if cleaned["category"] else None,
            budget_month=cleaned["budget_month"],
        )
        chart_data = {
            "monthly": {
                "labels": [row.period.strftime("%Y-%m") for row in report.monthly],
                "income": [str(row.income) for row in report.monthly],
                "expense": [str(row.expense) for row in report.monthly],
            },
            "categories": {
                "labels": [row.label for row in report.categories],
                "values": [str(row.amount) for row in report.categories],
            },
            "daily": {
                "labels": [row.label for row in report.daily],
                "values": [str(row.amount) for row in report.daily],
            },
            "netFunds": {
                "labels": [row.label for row in report.net_funds],
                "values": [str(row.amount) for row in report.net_funds],
            },
            "credit": {
                "labels": [row.label for row in report.credit.monthly_purchases],
                "values": [str(row.amount) for row in report.credit.monthly_purchases],
            },
            "installments": {
                "labels": [row.month.strftime("%Y-%m") for row in report.installments],
                "actual": [str(row.actual) for row in report.installments],
                "planned": [str(row.planned) for row in report.installments],
            },
            "savings": {
                "labels": [row.month.strftime("%Y-%m") for row in report.savings],
                "target": [str(row.target) for row in report.savings],
                "surplus": [str(row.income_expense_surplus) for row in report.savings],
            },
        }
    return render(
        request,
        "analytics/reports.html",
        {"form": form, "report": report, "chart_data": chart_data},
    )


@require_GET
def risk_overview(request: HttpRequest):
    form_data = request.GET or {
        "as_of": timezone.localdate().isoformat(),
        "month_count": "6",
    }
    form = ForecastForm(form_data)
    capacity = forecast = alerts = None
    if form.is_valid():
        try:
            capacity = services.repayment_capacity(as_of=form.cleaned_data["as_of"])
            forecast = services.forecast_cash_flow(**form.cleaned_data)
            alerts = services.risk_alerts(
                as_of=form.cleaned_data["as_of"],
                forecast_months=form.cleaned_data["month_count"],
            )
        except ValidationError as error:
            _add_error(form, error)
    return render(
        request,
        "analytics/risk_overview.html",
        {"form": form, "capacity": capacity, "forecast": forecast, "alerts": alerts},
    )


@require_http_methods(["GET", "POST"])
def installment_preview(request: HttpRequest):
    form = InstallmentPreviewForm(
        request.POST or None,
        initial={
            "as_of": timezone.localdate(),
            "month_count": 12,
            "first_month": timezone.localdate().replace(day=1),
        },
    )
    preview = None
    if request.method == "POST" and form.is_valid():
        try:
            preview = services.preview_installment(**form.cleaned_data)
        except ValidationError as error:
            _add_error(form, error)
    return render(
        request,
        "analytics/installment_preview.html",
        {"form": form, "preview": preview},
    )
