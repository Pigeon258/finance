from collections.abc import Callable
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction as db_transaction
from django.db.models import Sum
from django.utils import timezone

from apps.accounts.models import Account, AccountReconciliation

from .models import Category, Merchant, Tag, Transaction, TransactionEntry, TransactionTag

CENT = Decimal("0.01")
MAX_MONEY = Decimal("999999999999.99")


def normalized_necessity(*, category_type: str, necessity: str | None) -> str | None:
    if category_type == Category.CategoryType.INCOME:
        return None
    return necessity


@db_transaction.atomic
def create_category(
    *,
    name: str,
    category_type: str,
    necessity: str | None,
    default_budget: Decimal,
    is_active: bool,
    sort_order: int,
) -> Category:
    category = Category(
        name=name,
        category_type=category_type,
        necessity=normalized_necessity(category_type=category_type, necessity=necessity),
        default_budget=default_budget,
        is_active=is_active,
        sort_order=sort_order,
    )
    category.full_clean()
    category.save()
    return category


@db_transaction.atomic
def update_category(
    *,
    category: Category,
    name: str,
    necessity: str | None,
    default_budget: Decimal,
    is_active: bool,
    sort_order: int,
) -> Category:
    category.name = name
    category.necessity = normalized_necessity(
        category_type=category.category_type, necessity=necessity
    )
    category.default_budget = default_budget
    category.is_active = is_active
    category.sort_order = sort_order
    category.full_clean()
    category.save(
        update_fields=[
            "name",
            "necessity",
            "default_budget",
            "is_active",
            "sort_order",
            "updated_at",
        ]
    )
    return category


@db_transaction.atomic
def deactivate_category(*, category: Category) -> Category:
    if not category.is_active:
        raise ValidationError("分类已经停用。")
    category.is_active = False
    category.full_clean()
    category.save(update_fields=["is_active", "updated_at"])
    return category


def _validate_decimal(value: Decimal, *, allow_negative: bool = False) -> Decimal:
    _validate_storable_decimal(value)
    if not allow_negative and value <= 0:
        raise ValidationError("交易金额必须大于零。")
    if allow_negative and value == 0:
        raise ValidationError("余额变化不得为零。")
    return value


def _validate_storable_decimal(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal):
        raise ValidationError("财务金额必须使用 Decimal。")
    if not value.is_finite():
        raise ValidationError("财务金额必须是有限十进制数。")
    try:
        quantized = value.quantize(CENT)
    except InvalidOperation as error:
        raise ValidationError("财务金额超出允许范围。") from error
    if quantized != value:
        raise ValidationError("财务金额必须精确到人民币分。")
    if abs(value) > MAX_MONEY:
        raise ValidationError("财务金额超出允许范围。")
    return value


def _validate_occurred_at(occurred_at) -> None:
    if not timezone.is_aware(occurred_at):
        raise ValidationError("交易时间必须包含时区。")


def _validate_account(account: Account, *, nature: str | None = None) -> None:
    if not account.is_active:
        raise ValidationError("停用账户不能用于新交易。")
    if nature is not None and account.balance_nature != nature:
        raise ValidationError("账户性质与交易类型不匹配。")


def _validate_category(category: Category, *, category_type: str) -> None:
    if not category.is_active:
        raise ValidationError("停用分类不能用于新交易。")
    if category.category_type != category_type:
        raise ValidationError("分类类型与交易类型不匹配。")


def _budget_month(occurred_at):
    return timezone.localdate(occurred_at).replace(day=1)


def _create_transaction(
    *,
    transaction_type: str,
    amount: Decimal,
    occurred_at,
    category: Category | None,
    channel: str,
    counterparty: str,
    note: str,
    source: str,
    budget_month=None,
    merchant: Merchant | None = None,
    related_transaction: Transaction | None = None,
    tags: list[Tag] | tuple[Tag, ...] = (),
) -> Transaction:
    _validate_decimal(amount)
    _validate_occurred_at(occurred_at)
    ledger_transaction = Transaction(
        transaction_type=transaction_type,
        amount=amount,
        occurred_at=occurred_at,
        budget_month=budget_month,
        category=category,
        channel=channel,
        merchant=merchant,
        counterparty=counterparty,
        note=note,
        source=source,
        related_transaction=related_transaction,
    )
    ledger_transaction.full_clean()
    ledger_transaction.save()
    for tag in tags:
        if not tag.is_active:
            raise ValidationError("停用标签不能用于新交易。")
        TransactionTag.objects.create(transaction=ledger_transaction, tag=tag)
    return ledger_transaction


def _create_entry(
    *, transaction: Transaction, account: Account, balance_delta: Decimal, note: str = ""
) -> TransactionEntry:
    _validate_decimal(balance_delta, allow_negative=True)
    entry = TransactionEntry(
        transaction=transaction, account=account, balance_delta=balance_delta, note=note
    )
    entry.full_clean()
    entry.save()
    return entry


@db_transaction.atomic
def create_income(
    *,
    account: Account,
    category: Category,
    amount: Decimal,
    occurred_at,
    channel: str,
    counterparty: str = "",
    note: str = "",
    source: str = Transaction.Source.MANUAL,
    merchant: Merchant | None = None,
    tags: list[Tag] | tuple[Tag, ...] = (),
) -> Transaction:
    _validate_account(account, nature=Account.BalanceNature.ASSET)
    _validate_category(category, category_type=Category.CategoryType.INCOME)
    ledger_transaction = _create_transaction(
        transaction_type=Transaction.TransactionType.INCOME,
        amount=amount,
        occurred_at=occurred_at,
        budget_month=_budget_month(occurred_at),
        category=category,
        channel=channel,
        counterparty=counterparty,
        note=note,
        source=source,
        merchant=merchant,
        tags=tags,
    )
    _create_entry(transaction=ledger_transaction, account=account, balance_delta=amount)
    return ledger_transaction


@db_transaction.atomic
def create_expense(
    *,
    account: Account,
    category: Category,
    amount: Decimal,
    occurred_at,
    channel: str,
    counterparty: str = "",
    note: str = "",
    source: str = Transaction.Source.MANUAL,
    merchant: Merchant | None = None,
    tags: list[Tag] | tuple[Tag, ...] = (),
    budget_month=None,
) -> Transaction:
    _validate_account(account)
    _validate_category(category, category_type=Category.CategoryType.EXPENSE)
    ledger_transaction = _create_transaction(
        transaction_type=Transaction.TransactionType.EXPENSE,
        amount=amount,
        occurred_at=occurred_at,
        budget_month=budget_month or _budget_month(occurred_at),
        category=category,
        channel=channel,
        counterparty=counterparty,
        note=note,
        source=source,
        merchant=merchant,
        tags=tags,
    )
    delta = -amount if account.balance_nature == Account.BalanceNature.ASSET else amount
    _create_entry(transaction=ledger_transaction, account=account, balance_delta=delta)
    return ledger_transaction


@db_transaction.atomic
def create_credit_card_purchase(**kwargs) -> Transaction:
    account = kwargs.get("account")
    if account is None or account.balance_nature != Account.BalanceNature.LIABILITY:
        raise ValidationError("信用卡消费必须使用负债账户。")
    return create_expense(**kwargs)


@db_transaction.atomic
def create_transfer(
    *,
    source_account: Account,
    destination_account: Account,
    amount: Decimal,
    occurred_at,
    channel: str = Transaction.Channel.DIRECT,
    counterparty: str = "",
    note: str = "",
    source: str = Transaction.Source.MANUAL,
) -> Transaction:
    _validate_account(source_account, nature=Account.BalanceNature.ASSET)
    _validate_account(destination_account, nature=Account.BalanceNature.ASSET)
    if source_account.pk == destination_account.pk:
        raise ValidationError("转出和转入账户不能相同。")
    ledger_transaction = _create_transaction(
        transaction_type=Transaction.TransactionType.TRANSFER,
        amount=amount,
        occurred_at=occurred_at,
        category=None,
        channel=channel,
        counterparty=counterparty,
        note=note,
        source=source,
    )
    _create_entry(transaction=ledger_transaction, account=source_account, balance_delta=-amount)
    _create_entry(transaction=ledger_transaction, account=destination_account, balance_delta=amount)
    return ledger_transaction


@db_transaction.atomic
def create_credit_card_repayment(
    *,
    source_account: Account,
    credit_card_account: Account,
    amount: Decimal,
    occurred_at,
    channel: str = Transaction.Channel.BANK,
    note: str = "",
    source: str = Transaction.Source.MANUAL,
) -> Transaction:
    _validate_account(source_account, nature=Account.BalanceNature.ASSET)
    _validate_account(credit_card_account, nature=Account.BalanceNature.LIABILITY)
    ledger_transaction = _create_transaction(
        transaction_type=Transaction.TransactionType.TRANSFER,
        amount=amount,
        occurred_at=occurred_at,
        category=None,
        channel=channel,
        counterparty=credit_card_account.name,
        note=note,
        source=source,
    )
    _create_entry(transaction=ledger_transaction, account=source_account, balance_delta=-amount)
    _create_entry(
        transaction=ledger_transaction, account=credit_card_account, balance_delta=-amount
    )
    return ledger_transaction


@db_transaction.atomic
def create_refund(
    *,
    original_transaction: Transaction,
    amount: Decimal,
    occurred_at,
    account: Account | None = None,
    channel: str | None = None,
    note: str = "",
    source: str = Transaction.Source.MANUAL,
) -> Transaction:
    original = (
        Transaction.objects.select_for_update()
        .prefetch_related("entries")
        .get(pk=original_transaction.pk)
    )
    if original.transaction_type != Transaction.TransactionType.EXPENSE:
        raise ValidationError("退款只能关联支出交易。")
    if original.status != Transaction.Status.ACTIVE:
        raise ValidationError("只能退款有效支出。")
    _validate_decimal(amount)
    refunded_amount = Transaction.objects.filter(
        related_transaction=original,
        transaction_type=Transaction.TransactionType.REFUND,
        status=Transaction.Status.ACTIVE,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    if refunded_amount + amount > original.amount:
        raise ValidationError("累计退款金额不得超过原支出。")
    original_entry = original.entries.get()
    refund_account = account or original_entry.account
    _validate_account(refund_account, nature=original_entry.account.balance_nature)
    ledger_transaction = _create_transaction(
        transaction_type=Transaction.TransactionType.REFUND,
        amount=amount,
        occurred_at=occurred_at,
        budget_month=original.budget_month,
        category=original.category,
        channel=channel or original.channel,
        counterparty=original.counterparty,
        note=note,
        source=source,
        related_transaction=original,
    )
    delta = amount if refund_account.balance_nature == Account.BalanceNature.ASSET else -amount
    _create_entry(transaction=ledger_transaction, account=refund_account, balance_delta=delta)
    original.is_financial_locked = True
    original.save(update_fields=["is_financial_locked", "updated_at"])
    return ledger_transaction


@db_transaction.atomic
def merge_import_information(
    *, ledger_transaction: Transaction, channel: str, counterparty: str
) -> Transaction:
    """Fill non-financial gaps on a manual transaction from a reviewed import row."""
    target = Transaction.objects.select_for_update().get(pk=ledger_transaction.pk)
    if target.source != Transaction.Source.MANUAL or target.status != Transaction.Status.ACTIVE:
        raise ValidationError("只能向有效的手工交易合并导入信息。")
    update_fields = ["updated_at"]
    if not target.counterparty and counterparty:
        target.counterparty = counterparty
        update_fields.append("counterparty")
    if target.channel in {Transaction.Channel.DIRECT, Transaction.Channel.OTHER}:
        target.channel = channel
        update_fields.append("channel")
    target.full_clean()
    target.save(update_fields=update_fields)
    return target


@db_transaction.atomic
def create_balance_adjustment(
    *,
    account: Account,
    balance_delta: Decimal,
    occurred_at,
    reason: str,
    source: str = Transaction.Source.MANUAL,
    related_transaction: Transaction | None = None,
) -> Transaction:
    _validate_account(account)
    _validate_decimal(balance_delta, allow_negative=True)
    ledger_transaction = _create_transaction(
        transaction_type=Transaction.TransactionType.BALANCE_ADJUSTMENT,
        amount=abs(balance_delta),
        occurred_at=occurred_at,
        category=None,
        channel=Transaction.Channel.DIRECT,
        counterparty="",
        note=reason,
        source=source,
        related_transaction=related_transaction,
    )
    _create_entry(
        transaction=ledger_transaction, account=account, balance_delta=balance_delta, note=reason
    )
    return ledger_transaction


@db_transaction.atomic
def lock_transaction(*, ledger_transaction: Transaction) -> Transaction:
    locked = Transaction.objects.select_for_update().get(pk=ledger_transaction.pk)
    locked.is_financial_locked = True
    locked.save(update_fields=["is_financial_locked", "updated_at"])
    return locked


@db_transaction.atomic
def void_transaction(*, ledger_transaction: Transaction, reason: str) -> Transaction:
    target = Transaction.objects.select_for_update().get(pk=ledger_transaction.pk)
    if target.status != Transaction.Status.ACTIVE:
        raise ValidationError("只能作废有效交易。")
    if (
        target.is_financial_locked
        or target.related_transactions.filter(status=Transaction.Status.ACTIVE).exists()
    ):
        raise ValidationError("存在正式关联的交易不能直接作废。")
    if not reason.strip():
        raise ValidationError("作废原因不能为空。")
    target.status = Transaction.Status.VOID
    target.voided_at = timezone.now()
    target.void_reason = reason
    target.save(update_fields=["status", "voided_at", "void_reason", "updated_at"])
    return target


@db_transaction.atomic
def reverse_transaction(
    *, ledger_transaction: Transaction, occurred_at, reason: str
) -> list[Transaction]:
    target = (
        Transaction.objects.select_for_update()
        .prefetch_related("entries__account")
        .get(pk=ledger_transaction.pk)
    )
    if target.status != Transaction.Status.ACTIVE:
        raise ValidationError("只能反向修正有效交易。")
    if target.related_transactions.filter(status=Transaction.Status.ACTIVE).exists():
        raise ValidationError("存在有效关联交易时需先处理关联关系。")
    if not reason.strip():
        raise ValidationError("修正原因不能为空。")
    target.status = Transaction.Status.REVERSED
    target.is_financial_locked = True
    target.void_reason = reason
    target.save(update_fields=["status", "is_financial_locked", "void_reason", "updated_at"])
    reversals = []
    for entry in target.entries.all():
        reversals.append(
            create_balance_adjustment(
                account=entry.account,
                balance_delta=-entry.balance_delta,
                occurred_at=occurred_at,
                reason=reason,
                source=Transaction.Source.SYSTEM,
                related_transaction=target,
            )
        )
    return reversals


def _validate_manual_edit_target(target: Transaction) -> None:
    if target.status != Transaction.Status.ACTIVE:
        raise ValidationError("只能编辑有效交易。")
    if target.source != Transaction.Source.MANUAL:
        raise ValidationError("只能直接编辑手工交易。")
    if (
        target.is_financial_locked
        or target.related_transactions.filter(status=Transaction.Status.ACTIVE).exists()
    ):
        raise ValidationError("存在正式关联的交易不能直接编辑。")


def _rewrite_manual_transaction(
    *,
    ledger_transaction: Transaction,
    transaction_type: str,
    amount: Decimal,
    occurred_at,
    category: Category | None,
    budget_month,
    channel: str,
    counterparty: str,
    note: str,
    entries: list[tuple[Account, Decimal]],
    tags: list[Tag] | tuple[Tag, ...],
) -> Transaction:
    target = Transaction.objects.select_for_update().get(pk=ledger_transaction.pk)
    _validate_manual_edit_target(target)
    _validate_decimal(amount)
    _validate_occurred_at(occurred_at)
    for account, balance_delta in entries:
        _validate_account(account)
        _validate_decimal(balance_delta, allow_negative=True)
    for tag in tags:
        if not tag.is_active:
            raise ValidationError("停用标签不能用于交易。")

    target.transaction_type = transaction_type
    target.amount = amount
    target.occurred_at = occurred_at
    target.budget_month = budget_month
    target.category = category
    target.channel = channel
    target.counterparty = counterparty
    target.note = note
    target.merchant = None
    target.related_transaction = None
    target.full_clean()

    target.entries.all().delete()
    TransactionTag.objects.filter(transaction=target).delete()
    target.save(
        update_fields=[
            "transaction_type",
            "amount",
            "occurred_at",
            "budget_month",
            "category",
            "channel",
            "counterparty",
            "note",
            "merchant",
            "related_transaction",
            "updated_at",
        ]
    )
    for account, balance_delta in entries:
        _create_entry(transaction=target, account=account, balance_delta=balance_delta)
    for tag in tags:
        TransactionTag.objects.create(transaction=target, tag=tag)
    return target


@db_transaction.atomic
def update_income(
    *,
    ledger_transaction: Transaction,
    account: Account,
    category: Category,
    amount: Decimal,
    occurred_at,
    channel: str,
    counterparty: str = "",
    note: str = "",
    tags: list[Tag] | tuple[Tag, ...] = (),
) -> Transaction:
    _validate_account(account, nature=Account.BalanceNature.ASSET)
    _validate_category(category, category_type=Category.CategoryType.INCOME)
    return _rewrite_manual_transaction(
        ledger_transaction=ledger_transaction,
        transaction_type=Transaction.TransactionType.INCOME,
        amount=amount,
        occurred_at=occurred_at,
        category=category,
        budget_month=_budget_month(occurred_at),
        channel=channel,
        counterparty=counterparty,
        note=note,
        entries=[(account, amount)],
        tags=tags,
    )


@db_transaction.atomic
def update_expense(
    *,
    ledger_transaction: Transaction,
    account: Account,
    category: Category,
    amount: Decimal,
    occurred_at,
    channel: str,
    counterparty: str = "",
    note: str = "",
    tags: list[Tag] | tuple[Tag, ...] = (),
) -> Transaction:
    _validate_account(account)
    _validate_category(category, category_type=Category.CategoryType.EXPENSE)
    delta = -amount if account.balance_nature == Account.BalanceNature.ASSET else amount
    return _rewrite_manual_transaction(
        ledger_transaction=ledger_transaction,
        transaction_type=Transaction.TransactionType.EXPENSE,
        amount=amount,
        occurred_at=occurred_at,
        category=category,
        budget_month=_budget_month(occurred_at),
        channel=channel,
        counterparty=counterparty,
        note=note,
        entries=[(account, delta)],
        tags=tags,
    )


@db_transaction.atomic
def update_transfer(
    *,
    ledger_transaction: Transaction,
    source_account: Account,
    destination_account: Account,
    amount: Decimal,
    occurred_at,
    channel: str = Transaction.Channel.DIRECT,
    counterparty: str = "",
    note: str = "",
) -> Transaction:
    _validate_account(source_account, nature=Account.BalanceNature.ASSET)
    _validate_account(destination_account, nature=Account.BalanceNature.ASSET)
    if source_account.pk == destination_account.pk:
        raise ValidationError("转出和转入账户不能相同。")
    return _rewrite_manual_transaction(
        ledger_transaction=ledger_transaction,
        transaction_type=Transaction.TransactionType.TRANSFER,
        amount=amount,
        occurred_at=occurred_at,
        category=None,
        budget_month=None,
        channel=channel,
        counterparty=counterparty,
        note=note,
        entries=[(source_account, -amount), (destination_account, amount)],
        tags=(),
    )


@db_transaction.atomic
def update_credit_card_repayment(
    *,
    ledger_transaction: Transaction,
    source_account: Account,
    credit_card_account: Account,
    amount: Decimal,
    occurred_at,
    channel: str = Transaction.Channel.BANK,
    note: str = "",
) -> Transaction:
    _validate_account(source_account, nature=Account.BalanceNature.ASSET)
    _validate_account(credit_card_account, nature=Account.BalanceNature.LIABILITY)
    return _rewrite_manual_transaction(
        ledger_transaction=ledger_transaction,
        transaction_type=Transaction.TransactionType.TRANSFER,
        amount=amount,
        occurred_at=occurred_at,
        category=None,
        budget_month=None,
        channel=channel,
        counterparty=credit_card_account.name,
        note=note,
        entries=[(source_account, -amount), (credit_card_account, -amount)],
        tags=(),
    )


@db_transaction.atomic
def update_balance_adjustment(
    *,
    ledger_transaction: Transaction,
    account: Account,
    balance_delta: Decimal,
    occurred_at,
    reason: str,
) -> Transaction:
    _validate_account(account)
    _validate_decimal(balance_delta, allow_negative=True)
    return _rewrite_manual_transaction(
        ledger_transaction=ledger_transaction,
        transaction_type=Transaction.TransactionType.BALANCE_ADJUSTMENT,
        amount=abs(balance_delta),
        occurred_at=occurred_at,
        category=None,
        budget_month=None,
        channel=Transaction.Channel.DIRECT,
        counterparty="",
        note=reason,
        entries=[(account, balance_delta)],
        tags=(),
    )


@db_transaction.atomic
def reconcile_account(
    *,
    account: Account,
    actual_balance: Decimal,
    checked_at,
    note: str = "",
    create_adjustment: bool = False,
) -> AccountReconciliation:
    locked_account = Account.objects.select_for_update().get(pk=account.pk)
    if not locked_account.is_active:
        raise ValidationError("停用账户不能创建新的余额核对。")
    _validate_storable_decimal(actual_balance)
    _validate_occurred_at(checked_at)

    from .selectors import account_balance

    calculated_balance = account_balance(account=locked_account, as_of=checked_at)
    difference = actual_balance - calculated_balance
    _validate_storable_decimal(calculated_balance)
    _validate_storable_decimal(difference)
    reconciliation = AccountReconciliation(
        account=locked_account,
        actual_balance=actual_balance,
        calculated_balance=calculated_balance,
        difference=difference,
        checked_at=checked_at,
        note=note,
    )
    reconciliation.full_clean()
    reconciliation.save()

    if create_adjustment and difference != Decimal("0.00"):
        adjustment = create_balance_adjustment(
            account=locked_account,
            balance_delta=difference,
            occurred_at=checked_at,
            reason=note or "账户余额核对调整",
            source=Transaction.Source.MANUAL,
        )
        reconciliation.adjustment_transaction_id = adjustment.id
        reconciliation.save(update_fields=["adjustment_transaction_id"])
    return reconciliation


def _correct_locked_transaction(
    *,
    ledger_transaction: Transaction,
    correction_occurred_at,
    reason: str,
    replacement_creator: Callable[[], Transaction],
) -> tuple[Transaction, list[Transaction]]:
    target = Transaction.objects.select_for_update().get(pk=ledger_transaction.pk)
    if not target.is_financial_locked:
        raise ValidationError("未锁定交易应直接编辑，不需要反向修正。")
    reversals = reverse_transaction(
        ledger_transaction=target, occurred_at=correction_occurred_at, reason=reason
    )
    replacement = replacement_creator()
    replacement.related_transaction = target
    replacement.is_financial_locked = True
    replacement.full_clean()
    replacement.save(update_fields=["related_transaction", "is_financial_locked", "updated_at"])
    return replacement, reversals


@db_transaction.atomic
def correct_income(
    *, ledger_transaction: Transaction, correction_occurred_at, reason: str, **replacement_data
) -> tuple[Transaction, list[Transaction]]:
    return _correct_locked_transaction(
        ledger_transaction=ledger_transaction,
        correction_occurred_at=correction_occurred_at,
        reason=reason,
        replacement_creator=lambda: create_income(**replacement_data),
    )


@db_transaction.atomic
def correct_expense(
    *, ledger_transaction: Transaction, correction_occurred_at, reason: str, **replacement_data
) -> tuple[Transaction, list[Transaction]]:
    return _correct_locked_transaction(
        ledger_transaction=ledger_transaction,
        correction_occurred_at=correction_occurred_at,
        reason=reason,
        replacement_creator=lambda: create_expense(**replacement_data),
    )


@db_transaction.atomic
def correct_credit_card_purchase(
    *, ledger_transaction: Transaction, correction_occurred_at, reason: str, **replacement_data
) -> tuple[Transaction, list[Transaction]]:
    return _correct_locked_transaction(
        ledger_transaction=ledger_transaction,
        correction_occurred_at=correction_occurred_at,
        reason=reason,
        replacement_creator=lambda: create_credit_card_purchase(**replacement_data),
    )


@db_transaction.atomic
def correct_transfer(
    *, ledger_transaction: Transaction, correction_occurred_at, reason: str, **replacement_data
) -> tuple[Transaction, list[Transaction]]:
    return _correct_locked_transaction(
        ledger_transaction=ledger_transaction,
        correction_occurred_at=correction_occurred_at,
        reason=reason,
        replacement_creator=lambda: create_transfer(**replacement_data),
    )


@db_transaction.atomic
def correct_credit_card_repayment(
    *, ledger_transaction: Transaction, correction_occurred_at, reason: str, **replacement_data
) -> tuple[Transaction, list[Transaction]]:
    return _correct_locked_transaction(
        ledger_transaction=ledger_transaction,
        correction_occurred_at=correction_occurred_at,
        reason=reason,
        replacement_creator=lambda: create_credit_card_repayment(**replacement_data),
    )


@db_transaction.atomic
def correct_balance_adjustment(
    *, ledger_transaction: Transaction, correction_occurred_at, reason: str, **replacement_data
) -> tuple[Transaction, list[Transaction]]:
    return _correct_locked_transaction(
        ledger_transaction=ledger_transaction,
        correction_occurred_at=correction_occurred_at,
        reason=reason,
        replacement_creator=lambda: create_balance_adjustment(**replacement_data),
    )
