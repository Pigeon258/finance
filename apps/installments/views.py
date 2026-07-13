from uuid import uuid4

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.ledger import selectors as ledger_selectors

from . import selectors, services
from .forms import (
    EarlySettlementForm,
    ItemAdjustForm,
    ItemPostForm,
    PlanCreateForm,
    PostedRefundForm,
)
from .models import InstallmentItem, InstallmentPlan

TOKEN_SESSION_KEY = "installment_submission_tokens"


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


def _add_error(form, error: ValidationError) -> None:
    form.add_error(None, " ".join(error.messages))


@require_GET
def index(request: HttpRequest):
    return render(
        request,
        "installments/index.html",
        {
            "plans": selectors.plan_list(),
            "remaining": selectors.remaining_commitment(),
            "month_rows": selectors.future_month_summary(),
        },
    )


@require_http_methods(["GET", "POST"])
def plan_create(request: HttpRequest):
    form = PlanCreateForm(request.POST or None)
    if request.method == "POST":
        if not _consume_token(request):
            messages.warning(request, "该提交已处理或已失效。")
            return redirect("installments:index")
        if form.is_valid():
            try:
                plan = services.create_plan(**form.cleaned_data)
            except ValidationError as error:
                _add_error(form, error)
            else:
                messages.success(request, "分期计划已创建，尚未改变任何账户余额。")
                return redirect("installments:detail", plan_id=plan.id)
    return render(
        request,
        "installments/form.html",
        {
            "title": "创建商品分期",
            "form": form,
            "submission_token": _issue_token(request),
            "risk_placeholder": True,
        },
    )


@require_GET
def detail(request: HttpRequest, plan_id: int):
    plan = get_object_or_404(InstallmentPlan, pk=plan_id)
    plan = selectors.plan_detail(plan_id=plan.id)
    item_rows = []
    for item in plan.items.all():
        refundable = (
            ledger_selectors.refundable_remaining(original_transaction=item.ledger_transaction)
            if item.ledger_transaction_id
            else None
        )
        item_rows.append({"item": item, "refundable": refundable})
    return render(
        request,
        "installments/detail.html",
        {
            "plan": plan,
            "item_rows": item_rows,
            "remaining": selectors.remaining_commitment(plan=plan),
        },
    )


def _form_action(request, *, form, title: str, service_call, success: str, redirect_to):
    def redirect_result():
        if isinstance(redirect_to, tuple):
            return redirect(redirect_to[0], *redirect_to[1:])
        return redirect(redirect_to)

    if request.method == "POST":
        if not _consume_token(request):
            messages.warning(request, "该提交已处理或已失效。")
            return redirect_result()
        if form.is_valid():
            try:
                service_call(**form.cleaned_data)
            except ValidationError as error:
                _add_error(form, error)
            else:
                messages.success(request, success)
                return redirect_result()
    return render(
        request,
        "installments/form.html",
        {"title": title, "form": form, "submission_token": _issue_token(request)},
    )


@require_http_methods(["GET", "POST"])
def item_post(request: HttpRequest, item_id: int):
    item = get_object_or_404(InstallmentItem.objects.select_related("plan"), pk=item_id)
    form = ItemPostForm(
        request.POST or None,
        initial={"actual_amount": item.planned_amount, "occurred_at": timezone.localtime()},
    )
    return _form_action(
        request,
        form=form,
        title=f"{item.plan.product_name} 第 {item.sequence_number} 期入账",
        service_call=lambda **data: services.post_item(item=item, **data),
        success="分期期次已进入正式账本。",
        redirect_to=("installments:detail", item.plan_id),
    )


@require_http_methods(["GET", "POST"])
def item_adjust(request: HttpRequest, item_id: int):
    item = get_object_or_404(InstallmentItem.objects.select_related("plan"), pk=item_id)
    form = ItemAdjustForm(
        request.POST or None,
        initial={
            "new_amount": item.planned_amount,
            "new_due_date": item.due_date,
            "effective_date": timezone.localdate(),
        },
    )
    return _form_action(
        request,
        form=form,
        title=f"调整第 {item.sequence_number} 期",
        service_call=lambda **data: services.adjust_planned_item(item=item, **data),
        success="未来期次已调整。",
        redirect_to=("installments:detail", item.plan_id),
    )


@require_http_methods(["GET", "POST"])
def early_settlement(request: HttpRequest, plan_id: int):
    plan = get_object_or_404(InstallmentPlan, pk=plan_id)
    form = EarlySettlementForm(request.POST or None, initial={"occurred_at": timezone.localtime()})
    return _form_action(
        request,
        form=form,
        title=f"提前结清：{plan.product_name}",
        service_call=lambda **data: services.early_settle(plan=plan, **data),
        success="分期计划已按实际金额提前结清。",
        redirect_to=("installments:detail", plan.id),
    )


@require_POST
def cancel(request: HttpRequest, plan_id: int):
    plan = get_object_or_404(InstallmentPlan, pk=plan_id)
    try:
        services.cancel_plan(plan=plan, effective_date=timezone.localdate(), note="用户取消")
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, "分期计划已取消。")
    return redirect("installments:detail", plan_id=plan.id)


@require_POST
def refund_start(request: HttpRequest, plan_id: int):
    plan = get_object_or_404(InstallmentPlan, pk=plan_id)
    try:
        services.start_refund(plan=plan)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, "计划已进入退款处理中，请逐期登记实际结果。")
    return redirect("installments:detail", plan_id=plan.id)


@require_http_methods(["GET", "POST"])
def item_refund(request: HttpRequest, item_id: int):
    item = get_object_or_404(InstallmentItem.objects.select_related("plan"), pk=item_id)
    form = PostedRefundForm(request.POST or None, initial={"occurred_at": timezone.localtime()})
    return _form_action(
        request,
        form=form,
        title=f"登记第 {item.sequence_number} 期退款",
        service_call=lambda **data: services.refund_posted_item(item=item, **data),
        success="实际退款已进入账本。",
        redirect_to=("installments:detail", item.plan_id),
    )


@require_POST
def refund_finish(request: HttpRequest, plan_id: int):
    plan = get_object_or_404(InstallmentPlan, pk=plan_id)
    try:
        services.finish_refund(plan=plan)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, "退款处理已结束。")
    return redirect("installments:detail", plan_id=plan.id)
