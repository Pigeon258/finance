from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from . import selectors, services
from .forms import AccountForm
from .models import Account


def _add_service_error(form, error: ValidationError) -> None:
    if hasattr(error, "error_dict"):
        for field, errors in error.error_dict.items():
            target = field if field in form.fields else None
            for item in errors:
                form.add_error(target, item)
    else:
        form.add_error(None, error)


@require_GET
def account_index(request: HttpRequest):
    return render(
        request,
        "accounts/account_index.html",
        {"accounts": selectors.account_list()},
    )


@require_http_methods(["GET", "POST"])
def account_create(request: HttpRequest):
    form = AccountForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.create_account(**form.cleaned_data)
        except ValidationError as error:
            _add_service_error(form, error)
        else:
            return redirect("accounts:index")
    return render(request, "accounts/account_form.html", {"form": form, "is_create": True})


@require_http_methods(["GET", "POST"])
def account_edit(request: HttpRequest, account_id: int):
    account = get_object_or_404(Account, pk=account_id)
    form = AccountForm(request.POST or None, instance=account)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data.copy()
        data.pop("account_type")
        try:
            services.update_account(account=account, **data)
        except ValidationError as error:
            _add_service_error(form, error)
        else:
            return redirect("accounts:index")
    return render(request, "accounts/account_form.html", {"form": form, "is_create": False})


@require_POST
def account_deactivate(request: HttpRequest, account_id: int):
    account = get_object_or_404(Account, pk=account_id)
    try:
        services.deactivate_account(account=account)
    except ValidationError:
        pass
    return redirect("accounts:index")
