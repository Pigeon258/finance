from uuid import uuid4

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import HttpRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts import selectors as account_selectors
from apps.accounts.models import Account

from . import selectors, services
from .forms import (
    AccountReconciliationForm,
    BalanceAdjustmentForm,
    CategoryForm,
    CorrectionReasonForm,
    CreditCardExpenseForm,
    CreditCardRepaymentForm,
    ExpenseForm,
    IncomeForm,
    RefundForm,
    TagForm,
    TransactionFilterForm,
    TransactionTemplateForm,
    TransferForm,
    VoidTransactionForm,
)
from .models import Category, Tag, Transaction, TransactionTemplate

SUBMISSION_TOKEN_SESSION_KEY = "manual_transaction_submission_tokens"


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
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, "分类已停用。")
    return redirect("ledger:category-index")


@require_POST
def category_delete(request: HttpRequest, category_id: int):
    category = get_object_or_404(Category, pk=category_id)
    try:
        services.delete_category(category=category)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, "分类已删除。")
    return redirect("ledger:category-index")


@require_GET
def tag_index(request: HttpRequest):
    return render(
        request,
        "ledger/tag_index.html",
        {"tags": selectors.tag_list()},
    )


@require_http_methods(["GET", "POST"])
def tag_create(request: HttpRequest):
    form = TagForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.create_tag(**form.cleaned_data)
        except ValidationError as error:
            _add_service_error(form, error)
        else:
            messages.success(request, "标签已创建。")
            return redirect("ledger:tag-index")
    return render(request, "ledger/tag_form.html", {"form": form, "is_create": True})


@require_http_methods(["GET", "POST"])
def tag_edit(request: HttpRequest, tag_id: int):
    tag = get_object_or_404(Tag, pk=tag_id)
    form = TagForm(request.POST or None, instance=tag)
    if request.method == "POST" and form.is_valid():
        try:
            services.update_tag(tag=tag, **form.cleaned_data)
        except ValidationError as error:
            _add_service_error(form, error)
        else:
            messages.success(request, "标签已更新。")
            return redirect("ledger:tag-index")
    return render(request, "ledger/tag_form.html", {"form": form, "is_create": False})


@require_POST
def tag_toggle(request: HttpRequest, tag_id: int):
    tag = get_object_or_404(Tag, pk=tag_id)
    try:
        services.set_tag_active(tag=tag, is_active=not tag.is_active)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, "标签状态已更新。")
    return redirect("ledger:tag-index")


@require_POST
def tag_delete(request: HttpRequest, tag_id: int):
    tag = get_object_or_404(Tag, pk=tag_id)
    try:
        services.delete_tag(tag=tag)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, "标签已删除。")
    return redirect("ledger:tag-index")


def _issue_submission_token(request: HttpRequest) -> str:
    token = str(uuid4())
    tokens = request.session.get(SUBMISSION_TOKEN_SESSION_KEY, [])[-19:]
    request.session[SUBMISSION_TOKEN_SESSION_KEY] = [*tokens, token]
    return token


def _consume_submission_token(request: HttpRequest) -> bool:
    token = request.POST.get("submission_token")
    tokens = request.session.get(SUBMISSION_TOKEN_SESSION_KEY, [])
    if not token or token not in tokens:
        return False
    tokens.remove(token)
    request.session[SUBMISSION_TOKEN_SESSION_KEY] = tokens
    return True


MANUAL_CREATE_FORMS = {
    "income": {
        "title": "记录收入",
        "operation_label": "收入",
        "operation_hint": "资金流入所选资产账户，计入当月收入统计。",
        "operation_tone": "success",
        "submit_label": "保存收入",
        "form_class": IncomeForm,
    },
    "expense": {
        "title": "记录普通支出",
        "operation_label": "支出",
        "operation_hint": "资金从所选资产账户流出，计入当月支出与预算占用。",
        "operation_tone": "danger",
        "submit_label": "保存支出",
        "form_class": ExpenseForm,
    },
    "credit-card-expense": {
        "title": "记录信用卡消费",
        "operation_label": "信用卡消费",
        "operation_hint": "增加信用卡负债，并归入当前未出账账期。",
        "operation_tone": "warning",
        "submit_label": "保存信用卡消费",
        "form_class": CreditCardExpenseForm,
    },
    "transfer": {
        "title": "账户间转账",
        "operation_label": "转账",
        "operation_hint": "在资产账户之间划转资金，不改变收入、支出或预算。",
        "operation_tone": "info",
        "submit_label": "保存转账",
        "form_class": TransferForm,
    },
    "credit-card-repayment": {
        "title": "信用卡还款",
        "operation_label": "还款",
        "operation_hint": "从资金来源账户向信用卡账户转账，冲减已出账应还金额。",
        "operation_tone": "info",
        "submit_label": "保存还款",
        "form_class": CreditCardRepaymentForm,
    },
    "balance-adjustment": {
        "title": "余额调整",
        "operation_label": "余额调整",
        "operation_hint": "按实际余额修正账户差额，不计入收入、支出或预算。",
        "operation_tone": "neutral",
        "submit_label": "保存余额调整",
        "form_class": BalanceAdjustmentForm,
    },
}


def _create_manual_transaction(operation: str, cleaned_data: dict) -> Transaction:
    if operation == "income":
        return services.create_income(**cleaned_data)
    if operation == "expense":
        return services.create_expense(**cleaned_data)
    if operation == "credit-card-expense":
        return services.create_credit_card_purchase(**cleaned_data)
    if operation == "transfer":
        return services.create_transfer(**cleaned_data)
    if operation == "credit-card-repayment":
        return services.create_credit_card_repayment(**cleaned_data)
    if operation == "balance-adjustment":
        return services.create_balance_adjustment(**cleaned_data)
    raise ValueError(f"Unsupported manual operation: {operation}")


def _preferred_account(*, nature: str) -> Account | None:
    recent = selectors.recent_accounts(balance_nature=nature).first()
    if recent is not None:
        return recent
    return (
        Account.objects.filter(is_active=True, balance_nature=nature)
        .order_by("sort_order", "id")
        .first()
    )


def _recent_initial(operation: str) -> dict:
    asset = _preferred_account(nature=Account.BalanceNature.ASSET)
    liability = _preferred_account(nature=Account.BalanceNature.LIABILITY)
    if operation in {"income", "expense"}:
        return {"account": asset}
    if operation == "credit-card-expense":
        return {"account": liability}
    if operation == "transfer":
        destination = (
            Account.objects.filter(is_active=True, balance_nature=Account.BalanceNature.ASSET)
            .exclude(pk=asset.pk if asset else None)
            .order_by("sort_order", "id")
            .first()
        )
        return {"source_account": asset, "destination_account": destination}
    if operation == "credit-card-repayment":
        return {"source_account": asset, "credit_card_account": liability}
    if operation == "balance-adjustment":
        return {"account": asset}
    return {}


def _template_form_initial(template: TransactionTemplate) -> dict:
    common = {
        "amount": template.amount,
        "occurred_at": timezone.now(),
        "channel": template.channel,
        "counterparty": template.counterparty,
        "item_name": template.item_name,
        "note": template.note,
    }
    if template.operation in {"income", "expense", "credit-card-expense"}:
        return {
            **common,
            "account": template.primary_account,
            "category": template.category,
        }
    if template.operation == "transfer":
        return {
            **common,
            "source_account": template.primary_account,
            "destination_account": template.secondary_account,
        }
    return {
        "amount": template.amount,
        "occurred_at": timezone.now(),
        "source_account": template.primary_account,
        "credit_card_account": template.secondary_account,
        "channel": template.channel,
        "note": template.note,
    }


@require_http_methods(["GET", "POST"])
def transaction_create(request: HttpRequest, operation: str):
    form_config = MANUAL_CREATE_FORMS.get(operation)
    if form_config is None:
        return redirect("ledger:transaction-index")
    title = form_config["title"]
    form_class = form_config["form_class"]
    initial = _recent_initial(operation)
    template_id = request.GET.get("template")
    copy_id = request.GET.get("copy")
    if request.method == "GET" and template_id:
        template = get_object_or_404(TransactionTemplate, pk=template_id, is_active=True)
        if template.operation != operation:
            messages.error(request, "模板类型与当前操作不匹配。")
            return redirect("ledger:transaction-create", operation=template.operation)
        initial = _template_form_initial(template)
    elif request.method == "GET" and copy_id:
        source = get_object_or_404(Transaction, pk=copy_id)
        copied = _copy_initial(source)
        if copied is None or copied[0] != operation:
            messages.error(request, "该交易类型不能复制。")
            return redirect("ledger:transaction-index")
        initial = copied[1]
    form = form_class(request.POST or None, initial=initial)
    if request.method == "POST":
        if not _consume_submission_token(request):
            messages.warning(request, "该提交已处理或已失效。")
            return redirect("ledger:transaction-index")
        if form.is_valid():
            try:
                ledger_transaction = _create_manual_transaction(operation, form.cleaned_data)
            except ValidationError as error:
                _add_service_error(form, error)
            else:
                messages.success(request, "交易已记录。")
                return redirect("ledger:transaction-detail", transaction_id=ledger_transaction.id)
    submission_token = _issue_submission_token(request)
    return render(
        request,
        "ledger/transaction_form.html",
        {
            "form": form,
            "title": title,
            "submission_token": submission_token,
            "operation": operation,
            "operation_label": form_config["operation_label"],
            "operation_hint": form_config["operation_hint"],
            "operation_tone": form_config["operation_tone"],
            "submit_label": form_config["submit_label"],
        },
    )


@require_GET
def transaction_index(request: HttpRequest):
    filter_form = TransactionFilterForm(request.GET or None)
    filters = filter_form.cleaned_data if filter_form.is_valid() else {}
    paginator = Paginator(selectors.transaction_list(filters=filters), 25)
    page = paginator.get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return render(
        request,
        "ledger/transaction_index.html",
        {"filter_form": filter_form, "page": page, "filter_query": query.urlencode()},
    )


@require_GET
def transaction_detail(request: HttpRequest, transaction_id: int):
    try:
        ledger_transaction = selectors.transaction_detail(transaction_id=transaction_id)
    except Transaction.DoesNotExist:
        ledger_transaction = get_object_or_404(Transaction, pk=transaction_id)
    can_edit = _can_edit_transaction(ledger_transaction)
    remaining_refund = selectors.refundable_remaining(original_transaction=ledger_transaction)
    has_active_relations = ledger_transaction.related_transactions.filter(
        status=Transaction.Status.ACTIVE
    ).exists()
    can_void = (
        ledger_transaction.status == Transaction.Status.ACTIVE
        and ledger_transaction.source == Transaction.Source.MANUAL
        and not ledger_transaction.is_financial_locked
        and not has_active_relations
    )
    can_correct = (
        ledger_transaction.status == Transaction.Status.ACTIVE
        and ledger_transaction.source == Transaction.Source.MANUAL
        and ledger_transaction.is_financial_locked
        and not has_active_relations
        and ledger_transaction.transaction_type != Transaction.TransactionType.REFUND
    )
    return render(
        request,
        "ledger/transaction_detail.html",
        {
            "transaction": ledger_transaction,
            "can_edit": can_edit,
            "can_void": can_void,
            "can_correct": can_correct,
            "remaining_refund": remaining_refund,
            "void_form": VoidTransactionForm(),
            "can_copy": _copy_initial(ledger_transaction) is not None,
        },
    )


def _operation_for_transaction(ledger_transaction: Transaction) -> str | None:
    if ledger_transaction.transaction_type == Transaction.TransactionType.INCOME:
        return "income"
    if ledger_transaction.transaction_type == Transaction.TransactionType.EXPENSE:
        entry = ledger_transaction.entries.select_related("account").first()
        if entry and entry.account.balance_nature == Account.BalanceNature.LIABILITY:
            return "credit-card-expense"
        return "expense"
    if ledger_transaction.transaction_type == Transaction.TransactionType.TRANSFER:
        if ledger_transaction.entries.filter(
            account__balance_nature=Account.BalanceNature.LIABILITY
        ).exists():
            return "credit-card-repayment"
        return "transfer"
    if ledger_transaction.transaction_type == Transaction.TransactionType.BALANCE_ADJUSTMENT:
        return "balance-adjustment"
    return None


def _copy_initial(ledger_transaction: Transaction) -> tuple[str, dict] | None:
    operation = _operation_for_transaction(ledger_transaction)
    config = _edit_form_config(ledger_transaction)
    if operation is None or config is None:
        return None
    initial = dict(config[2])
    initial["occurred_at"] = timezone.now()
    return operation, initial


@require_GET
def transaction_copy(request: HttpRequest, transaction_id: int):
    ledger_transaction = get_object_or_404(Transaction, pk=transaction_id)
    copied = _copy_initial(ledger_transaction)
    if copied is None:
        messages.error(request, "退款等关联交易不能复制。")
        return redirect("ledger:transaction-detail", transaction_id=transaction_id)
    return redirect(
        f"{reverse('ledger:transaction-create', args=[copied[0]])}?copy={ledger_transaction.pk}"
    )


def _template_initial_from_transaction(ledger_transaction: Transaction) -> dict | None:
    copied = _copy_initial(ledger_transaction)
    if copied is None or copied[0] == "balance-adjustment":
        return None
    operation, initial = copied
    result = {
        "name": ledger_transaction.counterparty
        or ledger_transaction.get_transaction_type_display(),
        "operation": operation,
        "amount": ledger_transaction.amount,
        "category": ledger_transaction.category,
        "channel": ledger_transaction.channel,
        "counterparty": ledger_transaction.counterparty,
        "item_name": ledger_transaction.item_name,
        "note": ledger_transaction.note,
    }
    result["primary_account"] = initial.get("account") or initial.get("source_account")
    result["secondary_account"] = initial.get("destination_account") or initial.get(
        "credit_card_account"
    )
    return result


@require_GET
def transaction_template_index(request: HttpRequest):
    return render(
        request,
        "ledger/transaction_template_index.html",
        {"templates": selectors.transaction_template_list()},
    )


@require_http_methods(["GET", "POST"])
def transaction_template_edit(request: HttpRequest, template_id: int | None = None):
    template = get_object_or_404(TransactionTemplate, pk=template_id) if template_id else None
    initial = None
    source_id = request.GET.get("transaction")
    if template is None and source_id:
        source = get_object_or_404(Transaction, pk=source_id)
        initial = _template_initial_from_transaction(source)
        if initial is None:
            messages.error(request, "该交易不能保存为常用模板。")
            return redirect("ledger:transaction-detail", transaction_id=source.pk)
    form = TransactionTemplateForm(request.POST or None, instance=template, initial=initial)
    if request.method == "POST" and form.is_valid():
        candidate = form.save(commit=False)
        try:
            services.save_transaction_template(template=candidate)
        except ValidationError as error:
            _add_service_error(form, error)
        else:
            messages.success(request, "常用交易模板已保存。")
            return redirect("ledger:transaction-template-index")
    return render(request, "ledger/transaction_template_form.html", {"form": form})


def _can_edit_transaction(ledger_transaction: Transaction) -> bool:
    return (
        ledger_transaction.status == Transaction.Status.ACTIVE
        and ledger_transaction.source == Transaction.Source.MANUAL
        and not ledger_transaction.is_financial_locked
        and ledger_transaction.transaction_type != Transaction.TransactionType.REFUND
        and not ledger_transaction.related_transactions.filter(
            status=Transaction.Status.ACTIVE
        ).exists()
    )


def _edit_form_config(ledger_transaction: Transaction):
    entries = list(ledger_transaction.entries.select_related("account").all())
    common = {
        "amount": ledger_transaction.amount,
        "occurred_at": ledger_transaction.occurred_at,
        "channel": ledger_transaction.channel,
        "counterparty": ledger_transaction.counterparty,
        "item_name": ledger_transaction.item_name,
        "note": ledger_transaction.note,
    }
    if ledger_transaction.transaction_type == Transaction.TransactionType.INCOME:
        return (
            IncomeForm,
            services.update_income,
            {
                **common,
                "account": entries[0].account,
                "category": ledger_transaction.category,
                "tags": list(ledger_transaction.tags.all()),
            },
        )
    if ledger_transaction.transaction_type == Transaction.TransactionType.EXPENSE:
        form_class = (
            CreditCardExpenseForm
            if entries[0].account.balance_nature == "LIABILITY"
            else ExpenseForm
        )
        return (
            form_class,
            services.update_expense,
            {
                **common,
                "account": entries[0].account,
                "category": ledger_transaction.category,
                "tags": list(ledger_transaction.tags.all()),
            },
        )
    if ledger_transaction.transaction_type == Transaction.TransactionType.TRANSFER:
        liability_entries = [
            entry for entry in entries if entry.account.balance_nature == "LIABILITY"
        ]
        asset_entries = [entry for entry in entries if entry.account.balance_nature == "ASSET"]
        if liability_entries:
            return (
                CreditCardRepaymentForm,
                services.update_credit_card_repayment,
                {
                    "amount": ledger_transaction.amount,
                    "occurred_at": ledger_transaction.occurred_at,
                    "source_account": asset_entries[0].account,
                    "credit_card_account": liability_entries[0].account,
                    "channel": ledger_transaction.channel,
                    "note": ledger_transaction.note,
                },
            )
        source_entry = next(entry for entry in asset_entries if entry.balance_delta < 0)
        destination_entry = next(entry for entry in asset_entries if entry.balance_delta > 0)
        return (
            TransferForm,
            services.update_transfer,
            {
                **common,
                "source_account": source_entry.account,
                "destination_account": destination_entry.account,
            },
        )
    if ledger_transaction.transaction_type == Transaction.TransactionType.BALANCE_ADJUSTMENT:
        return (
            BalanceAdjustmentForm,
            services.update_balance_adjustment,
            {
                "account": entries[0].account,
                "balance_delta": entries[0].balance_delta,
                "occurred_at": ledger_transaction.occurred_at,
                "reason": ledger_transaction.note,
            },
        )
    return None


@require_http_methods(["GET", "POST"])
def transaction_edit(request: HttpRequest, transaction_id: int):
    ledger_transaction = get_object_or_404(Transaction, pk=transaction_id)
    if not _can_edit_transaction(ledger_transaction):
        messages.error(request, "该交易已锁定或存在正式关联，不能直接编辑。")
        return redirect("ledger:transaction-detail", transaction_id=transaction_id)
    config = _edit_form_config(ledger_transaction)
    if config is None:
        messages.error(request, "该交易类型不能在此编辑。")
        return redirect("ledger:transaction-detail", transaction_id=transaction_id)
    form_class, update_service, initial = config
    form = form_class(request.POST or None, initial=initial)
    if request.method == "POST":
        if not _consume_submission_token(request):
            messages.warning(request, "该提交已处理或已失效。")
            return redirect("ledger:transaction-detail", transaction_id=transaction_id)
        if form.is_valid():
            try:
                update_service(ledger_transaction=ledger_transaction, **form.cleaned_data)
            except ValidationError as error:
                _add_service_error(form, error)
            else:
                messages.success(request, "交易已更新。")
                return redirect("ledger:transaction-detail", transaction_id=transaction_id)
    submission_token = _issue_submission_token(request)
    return render(
        request,
        "ledger/transaction_form.html",
        {"form": form, "title": "编辑交易", "submission_token": submission_token},
    )


@require_POST
def transaction_void(request: HttpRequest, transaction_id: int):
    ledger_transaction = get_object_or_404(Transaction, pk=transaction_id)
    form = VoidTransactionForm(request.POST)
    if form.is_valid():
        try:
            services.void_transaction(
                ledger_transaction=ledger_transaction, reason=form.cleaned_data["reason"]
            )
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
        else:
            messages.success(request, "交易已作废。")
    else:
        messages.error(request, "请输入作废原因。")
    return redirect("ledger:transaction-detail", transaction_id=transaction_id)


@require_http_methods(["GET", "POST"])
def transaction_refund(request: HttpRequest, transaction_id: int):
    original = get_object_or_404(Transaction, pk=transaction_id)
    remaining = selectors.refundable_remaining(original_transaction=original)
    if remaining <= 0:
        messages.error(request, "该交易当前没有可退款金额。")
        return redirect("ledger:transaction-detail", transaction_id=transaction_id)
    form = RefundForm(
        request.POST or None,
        original_transaction=original,
        remaining_amount=remaining,
        initial={"channel": original.channel},
    )
    if request.method == "POST":
        if not _consume_submission_token(request):
            messages.warning(request, "该提交已处理或已失效。")
            return redirect("ledger:transaction-detail", transaction_id=transaction_id)
        if form.is_valid():
            try:
                refund = services.create_refund(original_transaction=original, **form.cleaned_data)
            except ValidationError as error:
                _add_service_error(form, error)
            else:
                messages.success(request, "退款已记录。")
                return redirect("ledger:transaction-detail", transaction_id=refund.id)
    submission_token = _issue_submission_token(request)
    return render(
        request,
        "ledger/refund_form.html",
        {
            "form": form,
            "original": original,
            "remaining": remaining,
            "submission_token": submission_token,
        },
    )


def _correction_service(ledger_transaction: Transaction):
    if ledger_transaction.transaction_type == Transaction.TransactionType.INCOME:
        return services.correct_income
    if ledger_transaction.transaction_type == Transaction.TransactionType.EXPENSE:
        entry = ledger_transaction.entries.select_related("account").get()
        if entry.account.balance_nature == Account.BalanceNature.LIABILITY:
            return services.correct_credit_card_purchase
        return services.correct_expense
    if ledger_transaction.transaction_type == Transaction.TransactionType.TRANSFER:
        if ledger_transaction.entries.filter(
            account__balance_nature=Account.BalanceNature.LIABILITY
        ).exists():
            return services.correct_credit_card_repayment
        return services.correct_transfer
    if ledger_transaction.transaction_type == Transaction.TransactionType.BALANCE_ADJUSTMENT:
        return services.correct_balance_adjustment
    return None


@require_http_methods(["GET", "POST"])
def transaction_correct(request: HttpRequest, transaction_id: int):
    ledger_transaction = get_object_or_404(Transaction, pk=transaction_id)
    config = _edit_form_config(ledger_transaction)
    correction_service = _correction_service(ledger_transaction)
    has_active_relations = ledger_transaction.related_transactions.filter(
        status=Transaction.Status.ACTIVE
    ).exists()
    if (
        config is None
        or correction_service is None
        or ledger_transaction.status != Transaction.Status.ACTIVE
        or not ledger_transaction.is_financial_locked
        or has_active_relations
    ):
        messages.error(request, "该交易当前不能执行反向修正。")
        return redirect("ledger:transaction-detail", transaction_id=transaction_id)

    form_class, _, initial = config
    transaction_form = form_class(request.POST or None, initial=initial)
    reason_form = CorrectionReasonForm(request.POST or None)
    if request.method == "POST":
        if not _consume_submission_token(request):
            messages.warning(request, "该提交已处理或已失效。")
            return redirect("ledger:transaction-detail", transaction_id=transaction_id)
        if transaction_form.is_valid() and reason_form.is_valid():
            try:
                replacement, _ = correction_service(
                    ledger_transaction=ledger_transaction,
                    correction_occurred_at=timezone.now(),
                    reason=reason_form.cleaned_data["correction_reason"],
                    **transaction_form.cleaned_data,
                )
            except ValidationError as error:
                _add_service_error(transaction_form, error)
            else:
                messages.success(request, "原交易已反向修正，并创建替代交易。")
                return redirect("ledger:transaction-detail", transaction_id=replacement.id)
    submission_token = _issue_submission_token(request)
    return render(
        request,
        "ledger/transaction_correction_form.html",
        {
            "transaction": ledger_transaction,
            "transaction_form": transaction_form,
            "reason_form": reason_form,
            "submission_token": submission_token,
        },
    )


@require_http_methods(["GET", "POST"])
def account_reconcile(request: HttpRequest, account_id: int):
    account = get_object_or_404(Account, pk=account_id)
    calculated_balance = selectors.account_balance(account=account)
    form = AccountReconciliationForm(
        request.POST or None,
        initial={"actual_balance": calculated_balance, "checked_at": timezone.now()},
    )
    if request.method == "POST":
        if not _consume_submission_token(request):
            messages.warning(request, "该提交已处理或已失效。")
            return redirect("ledger:account-reconcile", account_id=account_id)
        if form.is_valid():
            try:
                reconciliation = services.reconcile_account(account=account, **form.cleaned_data)
            except ValidationError as error:
                _add_service_error(form, error)
            else:
                messages.success(request, "账户余额核对已保存。")
                return redirect("ledger:account-reconcile", account_id=reconciliation.account_id)
    submission_token = _issue_submission_token(request)
    return render(
        request,
        "ledger/account_reconciliation.html",
        {
            "account": account,
            "calculated_balance": calculated_balance,
            "reconciliations": account_selectors.reconciliation_list(account=account)[:20],
            "form": form,
            "submission_token": submission_token,
        },
    )
