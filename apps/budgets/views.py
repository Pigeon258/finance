from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from . import selectors, services
from .forms import (
    BudgetItemForm,
    ConfirmOccurrenceForm,
    GenerateOccurrencesForm,
    MonthlyBudgetForm,
    PlannedCashFlowForm,
    ReserveMovementForm,
)
from .models import CategoryBudget, MonthlyBudget, PlannedCashFlow, PlannedCashFlowOccurrence

TOKEN_SESSION_KEY = "budget_submission_tokens"


def _issue_token(request: HttpRequest) -> str:
    token = str(uuid4())
    tokens = request.session.get(TOKEN_SESSION_KEY, [])[-29:]
    request.session[TOKEN_SESSION_KEY] = [*tokens, token]
    return token


def _consume_token(request: HttpRequest) -> bool:
    token = request.POST.get("submission_token")
    tokens = request.session.get(TOKEN_SESSION_KEY, [])
    if not token or token not in tokens:
        return False
    tokens.remove(token)
    request.session[TOKEN_SESSION_KEY] = tokens
    return True


def _month_from_query(request: HttpRequest) -> date:
    raw = request.GET.get("month", "")
    try:
        return date.fromisoformat(f"{raw}-01") if raw else timezone.localdate().replace(day=1)
    except ValueError:
        return timezone.localdate().replace(day=1)


def _previous_month(month: date) -> date:
    return date(month.year - 1, 12, 1) if month.month == 1 else date(month.year, month.month - 1, 1)


def _add_error(form, error: ValidationError) -> None:
    form.add_error(None, " ".join(error.messages))


@require_GET
def index(request: HttpRequest):
    month = _month_from_query(request)
    snapshot = selectors.monthly_snapshot(month=month)
    budget = snapshot["budget"]
    return render(
        request,
        "budgets/index.html",
        {
            "month": month,
            "snapshot": snapshot,
            "item_rows": selectors.budget_item_rows(month=month),
            "category_rows": selectors.category_budget_rows(budget=budget) if budget else [],
            "reserve_balance": selectors.reserve_balance(),
            "reserve_movements": selectors.reserve_movements()[:10],
            "occurrences": selectors.occurrence_list(month=month),
        },
    )


@require_http_methods(["GET", "POST"])
def monthly_budget_edit(request: HttpRequest):
    month = _month_from_query(request)
    budget = selectors.monthly_budget(month=month)
    initial = {
        "month": month,
        "savings_target": budget.savings_target if budget else Decimal("0.00"),
        "minimum_safety_buffer": budget.minimum_safety_buffer if budget else Decimal("0.00"),
        "note": budget.note if budget else "",
    }
    form = MonthlyBudgetForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            saved = services.save_monthly_budget(**form.cleaned_data)
        except ValidationError as error:
            _add_error(form, error)
        else:
            messages.success(request, "月度预算已保存。")
            return redirect(f"{reverse('budgets:index')}?month={saved.month:%Y-%m}")
    return render(request, "budgets/form.html", {"title": "设置月度预算", "form": form})


@require_POST
def copy_previous(request: HttpRequest):
    month = _month_from_query(request)
    try:
        services.copy_monthly_budget(source_month=_previous_month(month), target_month=month)
    except (ValidationError, MonthlyBudget.DoesNotExist) as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "已复制上月预算项目；已有项目设置保持不变。")
    return redirect(f"{reverse('budgets:index')}?month={month:%Y-%m}")


@require_GET
def budget_item_index(request: HttpRequest):
    month = _month_from_query(request)
    budget = selectors.monthly_budget(month=month)
    return render(
        request,
        "budgets/budget_items.html",
        {
            "month": month,
            "budget": budget,
            "items": selectors.budget_item_rows(month=month),
        },
    )


@require_http_methods(["GET", "POST"])
def budget_item_create(request: HttpRequest):
    month = _month_from_query(request)
    form = BudgetItemForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            item = services.create_budget_item(month=month, **form.cleaned_data)
        except ValidationError as error:
            _add_error(form, error)
        else:
            messages.success(request, f"预算项目“{item.name}”已创建，月度总预算已自动更新。")
            return redirect(f"{reverse('budgets:budget-item-index')}?month={month:%Y-%m}")
    return render(
        request,
        "budgets/budget_item_form.html",
        {"form": form, "title": "新增预算项目", "month": month},
    )


@require_http_methods(["GET", "POST"])
def budget_item_edit(request: HttpRequest, item_id: int):
    item = get_object_or_404(CategoryBudget, pk=item_id)
    form = BudgetItemForm(
        request.POST or None,
        initial={
            "name": item.name,
            "category": item.category,
            "budget_amount": item.budget_amount,
            "warning_threshold": item.warning_threshold,
            "sort_order": item.sort_order,
        },
    )
    if request.method == "POST" and form.is_valid():
        try:
            item = services.update_budget_item(item=item, **form.cleaned_data)
        except ValidationError as error:
            _add_error(form, error)
        else:
            messages.success(request, f"预算项目“{item.name}”已更新，月度总预算已自动更新。")
            return redirect(
                f"{reverse('budgets:budget-item-index')}?month={item.monthly_budget.month:%Y-%m}"
            )
    return render(
        request,
        "budgets/budget_item_form.html",
        {"form": form, "title": "编辑预算项目", "month": item.monthly_budget.month},
    )


@require_POST
def budget_item_delete(request: HttpRequest, item_id: int):
    item = get_object_or_404(CategoryBudget, pk=item_id)
    month = item.monthly_budget.month
    try:
        services.delete_budget_item(item=item)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, "预算项目已删除，月度总预算已自动更新。")
    return redirect(f"{reverse('budgets:budget-item-index')}?month={month:%Y-%m}")


@require_http_methods(["GET", "POST"])
def reserve_create(request: HttpRequest):
    form = ReserveMovementForm(request.POST or None, initial={"occurred_on": timezone.localdate()})
    if request.method == "POST" and form.is_valid():
        try:
            services.record_reserve_movement(**form.cleaned_data)
        except ValidationError as error:
            _add_error(form, error)
        else:
            messages.success(request, "储备变动已记录，不会直接改变账户余额。")
            return redirect("budgets:index")
    return render(request, "budgets/form.html", {"title": "记录储备变动", "form": form})


@require_GET
def cash_flow_index(request: HttpRequest):
    return render(
        request,
        "budgets/cash_flow_index.html",
        {
            "plans": selectors.planned_cash_flows(),
            "occurrences": selectors.occurrence_list(),
        },
    )


@require_http_methods(["GET", "POST"])
def cash_flow_create(request: HttpRequest):
    raw_direction = request.GET.get("direction", request.POST.get("direction"))
    direction = (
        raw_direction
        if raw_direction
        in {
            PlannedCashFlow.Direction.INCOME,
            PlannedCashFlow.Direction.EXPENSE,
        }
        else PlannedCashFlow.Direction.EXPENSE
    )
    form = PlannedCashFlowForm(request.POST or None, direction=direction)
    if request.method == "POST":
        if not _consume_token(request):
            messages.warning(request, "该提交已处理或已失效。")
            return redirect("budgets:cash-flow-index")
        if form.is_valid():
            data = form.cleaned_data.copy()
            data["direction"] = direction
            try:
                services.create_planned_cash_flow(**data)
            except ValidationError as error:
                _add_error(form, error)
            else:
                messages.success(request, "计划现金流已创建，并生成首个 12 个月内的事项。")
                return redirect("budgets:cash-flow-index")
    title = (
        "创建预计收入计划"
        if direction == PlannedCashFlow.Direction.INCOME
        else "创建固定支出计划"
    )
    return render(
        request,
        "budgets/form.html",
        {
            "title": title,
            "form": form,
            "submission_token": _issue_token(request),
        },
    )


@require_http_methods(["GET", "POST"])
def generate(request: HttpRequest, plan_id: int):
    plan = get_object_or_404(PlannedCashFlow, pk=plan_id)
    form = GenerateOccurrencesForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        generated = services.generate_occurrences(plan=plan, **form.cleaned_data)
        messages.success(request, f"新增 {len(generated)} 条计划事项；已有事项未重复生成。")
        return redirect("budgets:cash-flow-index")
    return render(request, "budgets/form.html", {"title": f"扩展计划：{plan.name}", "form": form})


@require_http_methods(["GET", "POST"])
def occurrence_confirm(request: HttpRequest, occurrence_id: int):
    occurrence = get_object_or_404(
        PlannedCashFlowOccurrence.objects.select_related("plan__default_account"), pk=occurrence_id
    )
    form = ConfirmOccurrenceForm(
        request.POST or None,
        initial={
            "account": occurrence.plan.default_account,
            "actual_amount": occurrence.planned_amount,
            "occurred_at": timezone.localtime(),
        },
    )
    if request.method == "POST":
        if not _consume_token(request):
            messages.warning(request, "该提交已处理或已失效。")
            return redirect("budgets:cash-flow-index")
        if form.is_valid():
            try:
                services.confirm_occurrence(occurrence=occurrence, **form.cleaned_data)
            except ValidationError as error:
                _add_error(form, error)
            else:
                messages.success(request, "计划事项已转换为正式交易。")
                return redirect("budgets:cash-flow-index")
    return render(
        request,
        "budgets/form.html",
        {
            "title": f"确认事项：{occurrence.plan.name}",
            "form": form,
            "submission_token": _issue_token(request),
        },
    )


@require_POST
def occurrence_skip(request: HttpRequest, occurrence_id: int):
    occurrence = get_object_or_404(PlannedCashFlowOccurrence, pk=occurrence_id)
    try:
        services.set_occurrence_status(
            occurrence=occurrence,
            status=PlannedCashFlowOccurrence.Status.SKIPPED,
            note="用户跳过",
        )
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, "计划事项已跳过。")
    return redirect("budgets:cash-flow-index")


@require_POST
def plan_toggle(request: HttpRequest, plan_id: int):
    plan = get_object_or_404(PlannedCashFlow, pk=plan_id)
    services.set_plan_active(plan=plan, is_active=not plan.is_active)
    messages.success(request, "计划状态已更新；已有事项保留。")
    return redirect("budgets:cash-flow-index")
