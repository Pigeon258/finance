
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.ledger import selectors as ledger_selectors

from . import selectors, services
from .forms import (
    TransferInForm,
    TransferOutForm,
    ValuationForm,
    WealthAccountForm,
    WealthIncomeForm,
)
from .models import WealthAccount


def _add_error(form, error: ValidationError) -> None:
    form.add_error(None, " ".join(error.messages))


@require_GET
def overview(request: HttpRequest):
    accounts = selectors.account_list()
    rows = []
    for account in accounts:
        principal = ledger_selectors.account_balance(account=account.core_account)
        rows.append(
            {
                "account": account,
                "principal": principal,
                "profit": account.current_value - principal,
            }
        )
    total_value = selectors.total_value()
    total_principal = selectors.total_principal()
    return render(
        request,
        "wealth/overview.html",
        {
            "rows": rows,
            "total_value": total_value,
            "total_principal": total_principal,
            "total_profit": total_value - total_principal,
            "month_income": selectors.month_income(),
            "recent_flows": selectors.flows()[:20],
        },
    )


@require_http_methods(["GET", "POST"])
def account_create(request: HttpRequest):
    form = WealthAccountForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.create_wealth_account(**form.cleaned_data)
        except ValidationError as error:
            _add_error(form, error)
        else:
            messages.success(request, "理财账户已创建。")
            return redirect("wealth:overview")
    return render(request, "wealth/account_form.html", {"form": form, "is_create": True})


@require_http_methods(["GET", "POST"])
def account_edit(request: HttpRequest, account_id: int):
    account = get_object_or_404(WealthAccount, pk=account_id)
    form = WealthAccountForm(request.POST or None, instance=account)
    if request.method == "POST" and form.is_valid():
        try:
            services.update_wealth_account(account=account, **form.cleaned_data)
        except ValidationError as error:
            _add_error(form, error)
        else:
            messages.success(request, "理财账户已更新。")
            return redirect("wealth:overview")
    return render(request, "wealth/account_form.html", {"form": form, "is_create": False})


@require_http_methods(["GET", "POST"])
def transfer_in(request: HttpRequest):
    form = TransferInForm(request.POST or None, initial={"occurred_at": timezone.now()})
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        account = WealthAccount.objects.filter(is_active=True).first()
        if account is None:
            messages.error(request, "请先创建理财账户。")
            return redirect("wealth:account-create")
        try:
            services.transfer_in(wealth_account=account, **data)
        except ValidationError as error:
            _add_error(form, error)
        else:
            messages.success(request, "已从日常账户转入理财账户。")
            return redirect("wealth:overview")
    return render(request, "wealth/transfer_form.html", {"form": form, "title": "转入理财"})


@require_http_methods(["GET", "POST"])
def transfer_out(request: HttpRequest):
    form = TransferOutForm(request.POST or None, initial={"occurred_at": timezone.now()})
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        account = WealthAccount.objects.filter(is_active=True).first()
        if account is None:
            messages.error(request, "请先创建理财账户。")
            return redirect("wealth:account-create")
        try:
            services.transfer_out(wealth_account=account, **data)
        except ValidationError as error:
            _add_error(form, error)
        else:
            messages.success(request, "已从理财账户转回日常账户。")
            return redirect("wealth:overview")
    return render(request, "wealth/transfer_form.html", {"form": form, "title": "转出理财"})


@require_http_methods(["GET", "POST"])
def valuation(request: HttpRequest, account_id: int):
    account = get_object_or_404(WealthAccount, pk=account_id)
    form = ValuationForm(
        request.POST or None,
        initial={
            "current_value": account.current_value,
            "valuation_date": account.valuation_date or timezone.localdate(),
        },
    )
    if request.method == "POST" and form.is_valid():
        try:
            services.update_valuation(wealth_account=account, **form.cleaned_data)
        except ValidationError as error:
            _add_error(form, error)
        else:
            messages.success(request, "理财账户估值已更新。")
            return redirect("wealth:overview")
    return render(request, "wealth/valuation_form.html", {"form": form, "account": account})


@require_http_methods(["GET", "POST"])
def income(request: HttpRequest, account_id: int):
    account = get_object_or_404(WealthAccount, pk=account_id)
    form = WealthIncomeForm(
        request.POST or None, initial={"occurred_on": timezone.localdate()}
    )
    if request.method == "POST" and form.is_valid():
        try:
            services.record_income(wealth_account=account, **form.cleaned_data)
        except ValidationError as error:
            _add_error(form, error)
        else:
            messages.success(request, "理财收益已记录。")
            return redirect("wealth:overview")
    return render(request, "wealth/income_form.html", {"form": form, "account": account})


@require_POST
def sync_yuebao(request: HttpRequest, account_id: int):
    account = get_object_or_404(WealthAccount, pk=account_id)
    try:
        account = services.sync_yuebao(wealth_account=account)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(
            request,
            f"余额宝收益率已同步：七日年化 {account.seven_day_annual_yield}%，"
            f"每万份收益 {account.per_ten_thousand_income} 元。",
        )
    return redirect("wealth:overview")
