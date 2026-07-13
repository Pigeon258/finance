import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction as db_transaction
from django.db.models import Case, IntegerField, Sum, When
from django.utils import timezone

from apps.accounts.models import Account
from apps.ledger import services as ledger_services
from apps.ledger.models import Transaction

from . import selectors
from .models import BillingCycle, BillingCycleItem, CreditCardProfile


def _validate_money(value: Decimal) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValidationError("信用卡金额必须使用有限 Decimal。")
    if value != value.quantize(Decimal("0.01")) or abs(value) > Decimal("999999999999.99"):
        raise ValidationError("信用卡金额必须精确到分且不超出范围。")


def _clamped_date(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _shift_month(year: int, month: int, offset: int) -> tuple[int, int]:
    month_index = year * 12 + month - 1 + offset
    return divmod(month_index, 12)[0], divmod(month_index, 12)[1] + 1


def cycle_dates_for(*, profile: CreditCardProfile, occurred_on: date) -> tuple[date, date, date]:
    current_statement = _clamped_date(occurred_on.year, occurred_on.month, profile.statement_day)
    if occurred_on <= current_statement:
        cycle_end = current_statement
    else:
        next_year, next_month = _shift_month(occurred_on.year, occurred_on.month, 1)
        cycle_end = _clamped_date(next_year, next_month, profile.statement_day)
    previous_year, previous_month = _shift_month(cycle_end.year, cycle_end.month, -1)
    previous_statement = _clamped_date(previous_year, previous_month, profile.statement_day)
    cycle_start = previous_statement + timedelta(days=1)
    due_year, due_month = _shift_month(cycle_end.year, cycle_end.month, 1)
    due_date = _clamped_date(due_year, due_month, profile.due_day)
    return cycle_start, cycle_end, due_date


def expected_due_date_for_month(*, profile: CreditCardProfile, due_month: date) -> date:
    if due_month.day != 1:
        raise ValidationError("预计还款月份必须使用该月第一天。")
    return _clamped_date(due_month.year, due_month.month, profile.due_day)


def _get_or_create_open_cycle_for_due_month(
    *, profile: CreditCardProfile, due_month: date
) -> BillingCycle:
    if due_month.day != 1:
        raise ValidationError("分期预算月份必须使用该月第一天。")
    existing = profile.billing_cycles.filter(
        due_date__year=due_month.year, due_date__month=due_month.month
    ).first()
    if existing is not None:
        if existing.status != BillingCycle.Status.OPEN:
            raise ValidationError("该分期期次对应的信用卡账期已经出账，不能直接加入。")
        return existing
    cycle_end_year, cycle_end_month = _shift_month(due_month.year, due_month.month, -1)
    cycle_end = _clamped_date(cycle_end_year, cycle_end_month, profile.statement_day)
    previous_year, previous_month = _shift_month(cycle_end.year, cycle_end.month, -1)
    cycle_start = _clamped_date(previous_year, previous_month, profile.statement_day) + timedelta(
        days=1
    )
    cycle, _ = BillingCycle.objects.get_or_create(
        credit_card_profile=profile,
        cycle_start=cycle_start,
        cycle_end=cycle_end,
        defaults={"due_date": expected_due_date_for_month(profile=profile, due_month=due_month)},
    )
    if cycle.status != BillingCycle.Status.OPEN or cycle.due_date.replace(day=1) != due_month:
        raise ValidationError("无法为该分期期次建立有效的未出账账期。")
    return cycle


@db_transaction.atomic
def save_profile(
    *,
    account: Account,
    credit_limit: Decimal,
    personal_monthly_limit: Decimal,
    statement_day: int,
    due_day: int,
    profile: CreditCardProfile | None = None,
) -> CreditCardProfile:
    if profile is None:
        profile = CreditCardProfile(account=account)
    else:
        profile = CreditCardProfile.objects.select_for_update().get(pk=profile.pk)
        if profile.billing_cycles.exists() and (
            profile.statement_day != statement_day or profile.due_day != due_day
        ):
            raise ValidationError("已有账期后不能直接修改账单日或还款日。")
        profile.account = account
    if not account.is_active or account.balance_nature != Account.BalanceNature.LIABILITY:
        raise ValidationError("信用卡配置必须关联启用的负债账户。")
    profile.credit_limit = credit_limit
    profile.personal_monthly_limit = personal_monthly_limit
    profile.statement_day = statement_day
    profile.due_day = due_day
    profile.is_active = True
    profile.full_clean()
    profile.save()
    refresh_open_cycle_assignments(profile=profile)
    return profile


def _get_or_create_open_cycle(*, profile: CreditCardProfile, occurred_on: date) -> BillingCycle:
    cycle_start, cycle_end, due_date = cycle_dates_for(profile=profile, occurred_on=occurred_on)
    cycle, _ = BillingCycle.objects.get_or_create(
        credit_card_profile=profile,
        cycle_start=cycle_start,
        cycle_end=cycle_end,
        defaults={"due_date": due_date},
    )
    if cycle.status != BillingCycle.Status.OPEN:
        raise ValidationError("该交易日期所属账期已经出账，不能直接加入。")
    return cycle


@db_transaction.atomic
def create_credit_card_purchase(*, profile: CreditCardProfile, **ledger_data) -> Transaction:
    locked_profile = CreditCardProfile.objects.select_for_update().get(pk=profile.pk)
    account = ledger_data.get("account")
    if account is None or account.pk != locked_profile.account_id:
        raise ValidationError("消费账户必须是当前信用卡账户。")
    occurred_at = ledger_data["occurred_at"]
    cycle = _get_or_create_open_cycle(
        profile=locked_profile, occurred_on=timezone.localdate(occurred_at)
    )
    ledger_transaction = ledger_services.create_credit_card_purchase(**ledger_data)
    BillingCycleItem.objects.create(
        billing_cycle=cycle,
        transaction=ledger_transaction,
        item_type=BillingCycleItem.ItemType.CHARGE,
        allocated_amount=ledger_transaction.amount,
    )
    return ledger_transaction


@db_transaction.atomic
def create_installment_purchase(
    *, profile: CreditCardProfile, due_month: date, **ledger_data
) -> tuple[Transaction, BillingCycle]:
    locked_profile = CreditCardProfile.objects.select_for_update().get(pk=profile.pk)
    account = ledger_data.get("account")
    if account is None or account.pk != locked_profile.account_id:
        raise ValidationError("分期期次必须使用当前信用卡账户。")
    cycle = _get_or_create_open_cycle_for_due_month(profile=locked_profile, due_month=due_month)
    ledger_transaction = ledger_services.create_expense(
        **ledger_data, budget_month=cycle.due_date.replace(day=1)
    )
    BillingCycleItem.objects.create(
        billing_cycle=cycle,
        transaction=ledger_transaction,
        item_type=BillingCycleItem.ItemType.INSTALLMENT,
        allocated_amount=ledger_transaction.amount,
    )
    return ledger_transaction, cycle


def _eligible_cycles_for_repayment(profile: CreditCardProfile):
    return (
        profile.billing_cycles.select_for_update()
        .filter(
            status__in=[
                BillingCycle.Status.ISSUED,
                BillingCycle.Status.PARTIALLY_PAID,
                BillingCycle.Status.OVERDUE,
            ]
        )
        .annotate(
            allocation_priority=Case(
                When(status=BillingCycle.Status.OVERDUE, then=0),
                default=1,
                output_field=IntegerField(),
            )
        )
        .order_by("allocation_priority", "due_date", "id")
    )


def _refresh_cycle_status(*, cycle: BillingCycle, as_of: date | None = None) -> BillingCycle:
    if cycle.status == BillingCycle.Status.OPEN:
        return cycle
    as_of = as_of or timezone.localdate()
    remaining = selectors.cycle_remaining_due(cycle=cycle)
    paid_or_credited = selectors.cycle_repaid_amount(
        cycle=cycle
    ) + selectors.cycle_confirmed_credit_amount(cycle=cycle)
    if remaining == 0:
        status = BillingCycle.Status.PAID
    elif cycle.due_date < as_of:
        status = BillingCycle.Status.OVERDUE
    elif paid_or_credited > 0:
        status = BillingCycle.Status.PARTIALLY_PAID
    else:
        status = BillingCycle.Status.ISSUED
    if cycle.status != status:
        cycle.status = status
        cycle.save(update_fields=["status", "updated_at"])
    return cycle


@db_transaction.atomic
def allocate_repayment_transaction(
    *, profile: CreditCardProfile, repayment: Transaction, as_of: date | None = None
) -> Decimal:
    repayment = Transaction.objects.select_for_update().get(pk=repayment.pk)
    if repayment.transaction_type != Transaction.TransactionType.TRANSFER:
        raise ValidationError("只有信用卡还款转账可以分配到账期。")
    account_ids = set(repayment.entries.values_list("account_id", flat=True))
    if profile.account_id not in account_ids:
        raise ValidationError("还款交易未影响当前信用卡账户。")
    already_allocated = repayment.billing_cycle_items.filter(
        item_type=BillingCycleItem.ItemType.REPAYMENT
    ).aggregate(total=Sum("allocated_amount"))["total"] or Decimal("0.00")
    available = repayment.amount - already_allocated
    if available <= 0:
        return Decimal("0.00")
    allocated_now = Decimal("0.00")
    for cycle in _eligible_cycles_for_repayment(profile):
        remaining = selectors.cycle_remaining_due(cycle=cycle)
        allocation = min(remaining, available)
        if allocation <= 0:
            continue
        BillingCycleItem.objects.create(
            billing_cycle=cycle,
            transaction=repayment,
            item_type=BillingCycleItem.ItemType.REPAYMENT,
            allocated_amount=allocation,
        )
        available -= allocation
        allocated_now += allocation
        _refresh_cycle_status(cycle=cycle, as_of=as_of)
        if available == 0:
            break
    if allocated_now > 0:
        ledger_services.lock_transaction(ledger_transaction=repayment)
    return allocated_now


@db_transaction.atomic
def create_credit_card_repayment(*, profile: CreditCardProfile, **ledger_data) -> Transaction:
    locked_profile = CreditCardProfile.objects.select_for_update().get(pk=profile.pk)
    credit_card_account = ledger_data.get("credit_card_account")
    if credit_card_account is None or credit_card_account.pk != locked_profile.account_id:
        raise ValidationError("还款目标必须是当前信用卡账户。")
    repayment = ledger_services.create_credit_card_repayment(**ledger_data)
    allocate_repayment_transaction(profile=locked_profile, repayment=repayment)
    return repayment


@db_transaction.atomic
def refresh_open_cycle_assignments(*, profile: CreditCardProfile) -> None:
    profile = CreditCardProfile.objects.select_for_update().get(pk=profile.pk)
    open_items = BillingCycleItem.objects.select_related("transaction").filter(
        billing_cycle__credit_card_profile=profile,
        billing_cycle__status=BillingCycle.Status.OPEN,
        item_type=BillingCycleItem.ItemType.CHARGE,
    )
    open_items.delete()
    transactions = Transaction.objects.filter(
        status=Transaction.Status.ACTIVE,
        transaction_type=Transaction.TransactionType.EXPENSE,
        entries__account=profile.account,
        billing_cycle_items__isnull=True,
    ).distinct()
    for ledger_transaction in transactions:
        cycle = _get_or_create_open_cycle(
            profile=profile,
            occurred_on=timezone.localdate(ledger_transaction.occurred_at),
        )
        BillingCycleItem.objects.create(
            billing_cycle=cycle,
            transaction=ledger_transaction,
            item_type=BillingCycleItem.ItemType.CHARGE,
            allocated_amount=ledger_transaction.amount,
        )


@db_transaction.atomic
def issue_cycle(
    *,
    cycle: BillingCycle,
    official_statement_amount: Decimal,
    official_due_amount: Decimal,
    due_date: date,
    note: str = "",
) -> BillingCycle:
    cycle = (
        BillingCycle.objects.select_for_update()
        .select_related("credit_card_profile")
        .get(pk=cycle.pk)
    )
    if cycle.status != BillingCycle.Status.OPEN:
        raise ValidationError("只有未出账账期可以确认出账。")
    _validate_money(official_statement_amount)
    _validate_money(official_due_amount)
    if official_statement_amount < 0 or official_due_amount < 0:
        raise ValidationError("正式账单金额和应还金额不得为负数。")
    if due_date <= cycle.cycle_end:
        raise ValidationError("还款日必须晚于账期结束日。")
    refresh_open_cycle_assignments(profile=cycle.credit_card_profile)
    cycle.refresh_from_db()
    cycle.official_statement_amount = official_statement_amount
    cycle.official_due_amount = official_due_amount
    cycle.due_date = due_date
    cycle.note = note
    cycle.issued_at = timezone.now()
    cycle.status = BillingCycle.Status.ISSUED
    cycle.full_clean()
    cycle.save()
    for item in cycle.items.filter(
        item_type__in=[
            BillingCycleItem.ItemType.CHARGE,
            BillingCycleItem.ItemType.INSTALLMENT,
            BillingCycleItem.ItemType.FEE,
        ]
    ).select_related("transaction"):
        ledger_services.lock_transaction(ledger_transaction=item.transaction)
    return _refresh_cycle_status(cycle=cycle)


@db_transaction.atomic
def confirm_refund_credit(*, cycle: BillingCycle, refund: Transaction) -> BillingCycleItem:
    cycle = BillingCycle.objects.select_for_update().get(pk=cycle.pk)
    if cycle.status == BillingCycle.Status.OPEN:
        raise ValidationError("正式账单退款冲抵只能用于已出账账期。")
    if refund.transaction_type != Transaction.TransactionType.REFUND:
        raise ValidationError("所选交易不是退款。")
    original_item = BillingCycleItem.objects.filter(
        billing_cycle=cycle,
        transaction=refund.related_transaction,
        item_type__in=[BillingCycleItem.ItemType.CHARGE, BillingCycleItem.ItemType.INSTALLMENT],
    ).first()
    if original_item is None:
        raise ValidationError("退款的原消费不属于该账期。")
    if refund.entries.get().account_id != cycle.credit_card_profile.account_id:
        raise ValidationError("退款未进入当前信用卡账户。")
    item = BillingCycleItem.objects.create(
        billing_cycle=cycle,
        transaction=refund,
        item_type=BillingCycleItem.ItemType.REFUND,
        allocated_amount=refund.amount,
    )
    ledger_services.lock_transaction(ledger_transaction=refund)
    _refresh_cycle_status(cycle=cycle)
    return item


def refresh_overdue_statuses(*, profile: CreditCardProfile, as_of: date | None = None) -> None:
    for cycle in profile.billing_cycles.exclude(status=BillingCycle.Status.OPEN):
        _refresh_cycle_status(cycle=cycle, as_of=as_of)
