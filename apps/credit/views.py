from uuid import uuid4

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.analytics import services as analytics_services
from apps.ledger.models import Transaction

from . import selectors, services
from .forms import CreditCardProfileForm, CreditPurchaseForm, IssueCycleForm, RepaymentForm
from .models import BillingCycle

TOKEN_SESSION_KEY = "credit_submission_tokens"


def _issue_token(request: HttpRequest) -> str:
    token = str(uuid4())
    tokens = request.session.get(TOKEN_SESSION_KEY, [])[-19:]
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
def overview(request: HttpRequest):
    profile = selectors.active_profile()
    if profile is None:
        return render(request, "credit/overview.html", {"profile": None})
    today = timezone.localdate()
    cycle_rows = []
    for cycle in profile.billing_cycles.all():
        effective_status = selectors.effective_cycle_status(cycle=cycle, as_of=today)
        cycle_rows.append(
            {
                "cycle": cycle,
                "status": effective_status,
                "status_label": selectors.cycle_status_label(status=effective_status),
                "status_tone": selectors.cycle_status_tone(status=effective_status),
                "calculated": selectors.cycle_calculated_statement_amount(cycle=cycle),
                "remaining": selectors.cycle_remaining_due(cycle=cycle),
            }
        )
    next_cycle = selectors.next_unpaid_cycle(profile=profile)
    return render(
        request,
        "credit/overview.html",
        {
            "profile": profile,
            "cycle_rows": cycle_rows,
            "liability": selectors.current_liability(profile=profile),
            "overpayment": selectors.overpayment(profile=profile),
            "issued_unpaid": selectors.issued_unpaid_amount(profile=profile),
            "unbilled": selectors.unbilled_amount(profile=profile),
            "next_cycle": next_cycle,
            "next_remaining": (
                selectors.cycle_remaining_due(cycle=next_cycle) if next_cycle else None
            ),
            "repayment_capacity": analytics_services.repayment_capacity(as_of=today),
        },
    )


@require_http_methods(["GET", "POST"])
def profile_settings(request: HttpRequest):
    profile = selectors.active_profile()
    form = CreditCardProfileForm(request.POST or None, instance=profile)
    if request.method == "POST" and form.is_valid():
        try:
            services.save_profile(profile=profile, **form.cleaned_data)
        except ValidationError as error:
            _add_error(form, error)
        else:
            messages.success(request, "信用卡设置已保存。")
            return redirect("credit:overview")
    return render(request, "credit/profile_form.html", {"form": form})


@require_http_methods(["GET", "POST"])
def purchase_create(request: HttpRequest):
    profile = selectors.active_profile()
    if profile is None:
        messages.error(request, "请先完成信用卡设置。")
        return redirect("credit:profile-settings")
    form = CreditPurchaseForm(
        request.POST or None,
        initial={"account": profile.account, "channel": Transaction.Channel.OTHER},
    )
    form.fields["account"].queryset = form.fields["account"].queryset.filter(pk=profile.account_id)
    if request.method == "POST":
        if not _consume_token(request):
            messages.warning(request, "该提交已处理或已失效。")
            return redirect("credit:overview")
        if form.is_valid():
            try:
                ledger_transaction = services.create_credit_card_purchase(
                    profile=profile, **form.cleaned_data
                )
            except ValidationError as error:
                _add_error(form, error)
            else:
                messages.success(request, "信用卡消费已记录并归入未出账账期。")
                return redirect("ledger:transaction-detail", transaction_id=ledger_transaction.id)
    return render(
        request,
        "credit/transaction_form.html",
        {"form": form, "title": "记录信用卡消费", "submission_token": _issue_token(request)},
    )


@require_http_methods(["GET", "POST"])
def repayment_create(request: HttpRequest):
    profile = selectors.active_profile()
    if profile is None:
        messages.error(request, "请先完成信用卡设置。")
        return redirect("credit:profile-settings")
    form = RepaymentForm(
        request.POST or None,
        initial={"credit_card_account": profile.account, "channel": Transaction.Channel.BANK},
    )
    form.fields["credit_card_account"].queryset = form.fields[
        "credit_card_account"
    ].queryset.filter(pk=profile.account_id)
    if request.method == "POST":
        if not _consume_token(request):
            messages.warning(request, "该提交已处理或已失效。")
            return redirect("credit:overview")
        if form.is_valid():
            try:
                repayment = services.create_credit_card_repayment(
                    profile=profile, **form.cleaned_data
                )
            except ValidationError as error:
                _add_error(form, error)
            else:
                unallocated = selectors.unallocated_repayment_amount(transaction=repayment)
                if unallocated:
                    messages.info(request, f"其中 {unallocated} 元未分配到账期，计为溢缴款。")
                messages.success(request, "信用卡还款已记录。")
                return redirect("ledger:transaction-detail", transaction_id=repayment.id)
    return render(
        request,
        "credit/transaction_form.html",
        {"form": form, "title": "信用卡还款", "submission_token": _issue_token(request)},
    )


@require_GET
def cycle_detail(request: HttpRequest, cycle_id: int):
    cycle = get_object_or_404(
        BillingCycle.objects.select_related("credit_card_profile__account").prefetch_related(
            "items__transaction"
        ),
        pk=cycle_id,
    )
    effective_status = selectors.effective_cycle_status(cycle=cycle, as_of=timezone.localdate())
    refund_candidates = Transaction.objects.filter(
        transaction_type=Transaction.TransactionType.REFUND,
        status=Transaction.Status.ACTIVE,
        related_transaction__billing_cycle_items__billing_cycle=cycle,
        billing_cycle_items__isnull=True,
        entries__account=cycle.credit_card_profile.account,
    ).distinct()
    return render(
        request,
        "credit/cycle_detail.html",
        {
            "cycle": cycle,
            "effective_status": effective_status,
            "status_label": selectors.cycle_status_label(status=effective_status),
            "status_tone": selectors.cycle_status_tone(status=effective_status),
            "calculated": selectors.cycle_calculated_statement_amount(cycle=cycle),
            "due_base": selectors.cycle_due_base(cycle=cycle),
            "repaid": selectors.cycle_repaid_amount(cycle=cycle),
            "credited": selectors.cycle_confirmed_credit_amount(cycle=cycle),
            "remaining": selectors.cycle_remaining_due(cycle=cycle),
            "refund_candidates": refund_candidates,
        },
    )


@require_http_methods(["GET", "POST"])
def cycle_issue(request: HttpRequest, cycle_id: int):
    cycle = get_object_or_404(BillingCycle, pk=cycle_id)
    if cycle.status != BillingCycle.Status.OPEN:
        messages.error(request, "该账期已经出账。")
        return redirect("credit:cycle-detail", cycle_id=cycle.id)
    form = IssueCycleForm(
        request.POST or None,
        initial={
            "official_statement_amount": selectors.cycle_calculated_statement_amount(cycle=cycle),
            "official_due_amount": selectors.cycle_calculated_statement_amount(cycle=cycle),
            "due_date": cycle.due_date,
        },
    )
    if request.method == "POST":
        if not _consume_token(request):
            messages.warning(request, "该提交已处理或已失效。")
            return redirect("credit:cycle-detail", cycle_id=cycle.id)
        if form.is_valid():
            try:
                services.issue_cycle(cycle=cycle, **form.cleaned_data)
            except ValidationError as error:
                _add_error(form, error)
            else:
                messages.success(request, "账期已按银行正式金额确认出账。")
                return redirect("credit:cycle-detail", cycle_id=cycle.id)
    return render(
        request,
        "credit/issue_form.html",
        {"cycle": cycle, "form": form, "submission_token": _issue_token(request)},
    )


@require_POST
def confirm_refund(request: HttpRequest, cycle_id: int, refund_id: int):
    cycle = get_object_or_404(BillingCycle, pk=cycle_id)
    refund = get_object_or_404(Transaction, pk=refund_id)
    try:
        services.confirm_refund_credit(cycle=cycle, refund=refund)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, "退款已确认冲抵该账期应还金额。")
    return redirect("credit:cycle-detail", cycle_id=cycle.id)
