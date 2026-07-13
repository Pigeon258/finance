from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from . import services
from .forms import ForecastForm, InstallmentPreviewForm


def _add_error(form, error: ValidationError) -> None:
    form.add_error(None, " ".join(error.messages))


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
