from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from . import selectors, services
from .forms import CategoryForm
from .models import Category


def _add_service_error(form, error: ValidationError) -> None:
    if hasattr(error, "error_dict"):
        for field, errors in error.error_dict.items():
            target = field if field in form.fields else None
            for item in errors:
                form.add_error(target, item)
    else:
        form.add_error(None, error)


@require_GET
def category_index(request: HttpRequest):
    return render(
        request,
        "ledger/category_index.html",
        {"categories": selectors.category_list()},
    )


@require_http_methods(["GET", "POST"])
def category_create(request: HttpRequest):
    form = CategoryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.create_category(**form.cleaned_data)
        except ValidationError as error:
            _add_service_error(form, error)
        else:
            return redirect("ledger:category-index")
    return render(request, "ledger/category_form.html", {"form": form, "is_create": True})


@require_http_methods(["GET", "POST"])
def category_edit(request: HttpRequest, category_id: int):
    category = get_object_or_404(Category, pk=category_id)
    form = CategoryForm(request.POST or None, instance=category)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data.copy()
        data.pop("category_type")
        try:
            services.update_category(category=category, **data)
        except ValidationError as error:
            _add_service_error(form, error)
        else:
            return redirect("ledger:category-index")
    return render(request, "ledger/category_form.html", {"form": form, "is_create": False})


@require_POST
def category_deactivate(request: HttpRequest, category_id: int):
    category = get_object_or_404(Category, pk=category_id)
    try:
        services.deactivate_category(category=category)
    except ValidationError:
        pass
    return redirect("ledger:category-index")
