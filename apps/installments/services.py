import calendar
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction as db_transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.accounts.models import Account
from apps.credit import selectors as credit_selectors
from apps.credit import services as credit_services
from apps.ledger import services as ledger_services
from apps.ledger.models import Category, Transaction

from .models import InstallmentAdjustment, InstallmentItem, InstallmentPlan

MONEY_QUANTUM = Decimal("0.01")
MAX_MONEY = Decimal("999999999999.99")


def _validate_money(value: Decimal, *, allow_zero: bool = False) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValidationError("分期金额必须使用有限 Decimal。")
    if abs(value) > MAX_MONEY or value != value.quantize(MONEY_QUANTUM):
        raise ValidationError("分期金额必须精确到分且不超出范围。")
    if value < 0 or (value == 0 and not allow_zero):
        raise ValidationError("分期金额必须大于零。")


def _shift_month(month: date, offset: int) -> tuple[int, int]:
    month_index = month.year * 12 + month.month - 1 + offset
    return divmod(month_index, 12)[0], divmod(month_index, 12)[1] + 1


def _clamped_month_date(first_due_date: date, offset: int) -> date:
    year, month = _shift_month(first_due_date.replace(day=1), offset)
    day = min(first_due_date.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _validate_expense_category(category: Category) -> None:
    if not category.is_active or category.category_type != Category.CategoryType.EXPENSE:
        raise ValidationError("分期计划必须使用启用的支出分类。")


def _create_adjustment(
    *,
    plan: InstallmentPlan,
    adjustment_type: str,
    amount_delta: Decimal,
    effective_date: date,
    item: InstallmentItem | None = None,
    related_transaction: Transaction | None = None,
    note: str = "",
) -> InstallmentAdjustment:
    adjustment = InstallmentAdjustment(
        plan=plan,
        installment_item=item,
        adjustment_type=adjustment_type,
        amount_delta=amount_delta,
        effective_date=effective_date,
        related_transaction=related_transaction,
        note=note,
    )
    adjustment.full_clean()
    adjustment.save()
    return adjustment


def _recalculate_total(plan: InstallmentPlan) -> None:
    amounts = plan.items.aggregate(
        posted=Sum("actual_amount", filter=Q(status="POSTED")),
        planned=Sum("planned_amount", filter=Q(status="PLANNED")),
    )
    plan.total_repayment_amount = (amounts["posted"] or Decimal("0.00")) + (
        amounts["planned"] or Decimal("0.00")
    )
    if plan.total_repayment_amount > 0:
        plan.save(update_fields=["total_repayment_amount", "updated_at"])


@db_transaction.atomic
def create_plan(
    *,
    product_name: str,
    purchase_date: date,
    original_price: Decimal,
    category: Category,
    source_type: str,
    installment_count: int,
    default_installment_amount: Decimal,
    total_repayment_amount: Decimal | None = None,
    first_due_month: date | None = None,
    first_due_date: date | None = None,
    note: str = "",
) -> InstallmentPlan:
    _validate_money(original_price)
    _validate_money(default_installment_amount)
    _validate_expense_category(category)
    if not product_name.strip():
        raise ValidationError("商品名称不能为空。")
    if installment_count < 1 or installment_count > 600:
        raise ValidationError("分期期数必须在 1 到 600 之间。")
    total = (
        total_repayment_amount
        if total_repayment_amount is not None
        else default_installment_amount * installment_count
    )
    _validate_money(total)
    final_amount = total - default_installment_amount * (installment_count - 1)
    if final_amount <= 0:
        raise ValidationError("总还款金额不足以生成最后一期正数金额。")
    _validate_money(final_amount)

    if source_type == InstallmentPlan.SourceType.CREDIT_CARD:
        if first_due_month is None or first_due_month.day != 1:
            raise ValidationError("信用卡分期必须选择首期月份。")
        profile = credit_selectors.active_profile()
        if profile is None:
            raise ValidationError("创建信用卡分期前必须先完成信用卡设置。")
        first_item_due_date = credit_services.expected_due_date_for_month(
            profile=profile, due_month=first_due_month
        )
    elif source_type == InstallmentPlan.SourceType.PLATFORM:
        if first_due_date is None:
            raise ValidationError("平台分期必须填写首期具体到期日。")
        first_item_due_date = first_due_date
        first_due_month = first_due_date.replace(day=1)
    else:
        raise ValidationError("不支持的分期来源。")

    plan = InstallmentPlan(
        product_name=product_name.strip(),
        purchase_date=purchase_date,
        original_price=original_price,
        category=category,
        source_type=source_type,
        installment_count=installment_count,
        default_installment_amount=default_installment_amount,
        first_due_month=first_due_month,
        total_repayment_amount=total,
        note=note,
    )
    plan.full_clean()
    plan.save()
    for offset in range(installment_count):
        if source_type == InstallmentPlan.SourceType.CREDIT_CARD:
            year, month = _shift_month(first_due_month, offset)
            due_month = date(year, month, 1)
            due_date = credit_services.expected_due_date_for_month(
                profile=profile, due_month=due_month
            )
        else:
            due_date = _clamped_month_date(first_item_due_date, offset)
            due_month = due_date.replace(day=1)
        item = InstallmentItem(
            plan=plan,
            sequence_number=offset + 1,
            due_date=due_date,
            due_month=due_month,
            planned_amount=(
                final_amount if offset == installment_count - 1 else default_installment_amount
            ),
        )
        item.full_clean()
        item.save()
    return plan


def _refresh_completion(plan: InstallmentPlan) -> None:
    if (
        plan.status == InstallmentPlan.Status.ACTIVE
        and not plan.items.filter(status=InstallmentItem.Status.PLANNED).exists()
    ):
        plan.status = InstallmentPlan.Status.COMPLETED
        plan.save(update_fields=["status", "updated_at"])


@db_transaction.atomic
def post_item(
    *,
    item: InstallmentItem,
    actual_amount: Decimal,
    occurred_at,
    account: Account | None = None,
    note: str = "",
) -> InstallmentItem:
    _validate_money(actual_amount)
    item = (
        InstallmentItem.objects.select_for_update().select_related("plan__category").get(pk=item.pk)
    )
    plan = InstallmentPlan.objects.select_for_update().get(pk=item.plan_id)
    if plan.status != InstallmentPlan.Status.ACTIVE:
        raise ValidationError("只有进行中的分期计划可以将期次入账。")
    if item.status != InstallmentItem.Status.PLANNED:
        raise ValidationError("该期次已经处理，不能重复入账。")

    ledger_data = {
        "account": account,
        "category": item.plan.category,
        "amount": actual_amount,
        "occurred_at": occurred_at,
        "channel": Transaction.Channel.OTHER,
        "counterparty": item.plan.product_name,
        "note": note,
        "source": Transaction.Source.MANUAL,
    }
    cycle = None
    if plan.source_type == InstallmentPlan.SourceType.CREDIT_CARD:
        profile = credit_selectors.active_profile()
        if profile is None:
            raise ValidationError("当前没有启用的信用卡配置。")
        ledger_data["account"] = profile.account
        ledger_transaction, cycle = credit_services.create_installment_purchase(
            profile=profile, due_month=item.due_month, **ledger_data
        )
        item.due_date = cycle.due_date
        item.due_month = cycle.due_date.replace(day=1)
    else:
        if account is None or account.balance_nature != Account.BalanceNature.ASSET:
            raise ValidationError("平台分期期次必须选择启用的资产账户。")
        ledger_transaction = ledger_services.create_expense(
            **ledger_data, budget_month=item.due_month
        )

    item.actual_amount = actual_amount
    item.ledger_transaction = ledger_transaction
    item.billing_cycle = cycle
    item.posted_at = timezone.now()
    item.status = InstallmentItem.Status.POSTED
    item.note = note
    item.full_clean()
    item.save()
    _recalculate_total(plan)
    _refresh_completion(plan)
    return item


@db_transaction.atomic
def adjust_planned_item(
    *,
    item: InstallmentItem,
    new_amount: Decimal,
    new_due_date: date,
    effective_date: date,
    note: str = "",
) -> InstallmentItem:
    _validate_money(new_amount, allow_zero=True)
    item = InstallmentItem.objects.select_for_update().select_related("plan").get(pk=item.pk)
    plan = InstallmentPlan.objects.select_for_update().get(pk=item.plan_id)
    if plan.status not in [
        InstallmentPlan.Status.ACTIVE,
        InstallmentPlan.Status.REFUND_PROCESSING,
    ]:
        raise ValidationError("当前计划状态不允许调整未来期次。")
    if item.status != InstallmentItem.Status.PLANNED:
        raise ValidationError("只能调整尚未入账的期次。")
    old_amount = item.planned_amount
    if new_amount == old_amount and new_due_date == item.due_date:
        raise ValidationError("金额或预计到期日至少需要修改一项。")
    if plan.status == InstallmentPlan.Status.REFUND_PROCESSING and new_amount > old_amount:
        raise ValidationError("退款处理中只能减少未来期次金额。")
    item.status = (
        InstallmentItem.Status.WAIVED if new_amount == 0 else InstallmentItem.Status.PLANNED
    )
    if new_amount > 0:
        item.planned_amount = new_amount
    item.due_date = new_due_date
    item.due_month = new_due_date.replace(day=1)
    item.full_clean()
    item.save()
    amount_delta = new_amount - old_amount
    if amount_delta == 0:
        adjustment_type = InstallmentAdjustment.AdjustmentType.MANUAL_CORRECTION
    elif plan.status == InstallmentPlan.Status.REFUND_PROCESSING:
        adjustment_type = InstallmentAdjustment.AdjustmentType.REFUND
    else:
        adjustment_type = InstallmentAdjustment.AdjustmentType.AMOUNT_CHANGE
    _create_adjustment(
        plan=plan,
        item=item,
        adjustment_type=adjustment_type,
        amount_delta=amount_delta,
        effective_date=effective_date,
        note=note,
    )
    _recalculate_total(plan)
    return item


@db_transaction.atomic
def cancel_plan(*, plan: InstallmentPlan, effective_date: date, note: str = "") -> InstallmentPlan:
    plan = InstallmentPlan.objects.select_for_update().get(pk=plan.pk)
    if plan.status != InstallmentPlan.Status.ACTIVE:
        raise ValidationError("只有进行中的计划可以取消。")
    if plan.items.filter(status=InstallmentItem.Status.POSTED).exists():
        raise ValidationError("已有期次入账的计划不能直接取消，请使用提前结清或退款调整。")
    items = list(plan.items.select_for_update().filter(status=InstallmentItem.Status.PLANNED))
    if not items:
        raise ValidationError("该计划没有可取消的未来期次。")
    amount_delta = -sum((item.planned_amount for item in items), Decimal("0.00"))
    for item in items:
        item.status = InstallmentItem.Status.CANCELLED
        item.save(update_fields=["status", "updated_at"])
    plan.status = InstallmentPlan.Status.CANCELLED
    plan.save(update_fields=["status", "updated_at"])
    _create_adjustment(
        plan=plan,
        adjustment_type=InstallmentAdjustment.AdjustmentType.CANCEL_REMAINING,
        amount_delta=amount_delta,
        effective_date=effective_date,
        note=note,
    )
    return plan


@db_transaction.atomic
def early_settle(
    *,
    plan: InstallmentPlan,
    amount: Decimal,
    occurred_at,
    account: Account | None = None,
    note: str = "",
) -> InstallmentPlan:
    _validate_money(amount)
    plan = InstallmentPlan.objects.select_for_update().select_related("category").get(pk=plan.pk)
    if plan.status != InstallmentPlan.Status.ACTIVE:
        raise ValidationError("只有进行中的计划可以提前结清。")
    remaining_items = list(
        plan.items.select_for_update().filter(status=InstallmentItem.Status.PLANNED)
    )
    if not remaining_items:
        raise ValidationError("该计划没有可提前结清的剩余期次。")
    planned_remaining = sum((item.planned_amount for item in remaining_items), Decimal("0.00"))
    ledger_data = {
        "account": account,
        "category": plan.category,
        "amount": amount,
        "occurred_at": occurred_at,
        "channel": Transaction.Channel.OTHER,
        "counterparty": plan.product_name,
        "note": note,
        "source": Transaction.Source.MANUAL,
    }
    if plan.source_type == InstallmentPlan.SourceType.CREDIT_CARD:
        profile = credit_selectors.active_profile()
        if profile is None:
            raise ValidationError("当前没有启用的信用卡配置。")
        ledger_data["account"] = profile.account
        _, _, due_date = credit_services.cycle_dates_for(
            profile=profile, occurred_on=timezone.localdate(occurred_at)
        )
        ledger_transaction, cycle = credit_services.create_installment_purchase(
            profile=profile, due_month=due_date.replace(day=1), **ledger_data
        )
    else:
        if account is None or account.balance_nature != Account.BalanceNature.ASSET:
            raise ValidationError("平台分期提前结清必须选择启用的资产账户。")
        ledger_transaction = ledger_services.create_expense(**ledger_data)
        cycle = None
    settlement_item = remaining_items[0]
    settlement_item.actual_amount = amount
    settlement_item.ledger_transaction = ledger_transaction
    settlement_item.billing_cycle = cycle
    settlement_item.posted_at = timezone.now()
    settlement_item.status = InstallmentItem.Status.POSTED
    settlement_item.note = note
    if cycle is not None:
        settlement_item.due_date = cycle.due_date
    else:
        settlement_item.due_date = timezone.localdate(occurred_at)
    settlement_item.due_month = settlement_item.due_date.replace(day=1)
    settlement_item.full_clean()
    settlement_item.save()
    for item in remaining_items[1:]:
        item.status = InstallmentItem.Status.CANCELLED
        item.save(update_fields=["status", "updated_at"])
    posted_total = plan.items.filter(status=InstallmentItem.Status.POSTED).aggregate(
        total=Sum("actual_amount")
    )["total"] or Decimal("0.00")
    plan.total_repayment_amount = posted_total
    plan.status = InstallmentPlan.Status.EARLY_SETTLED
    plan.save(update_fields=["total_repayment_amount", "status", "updated_at"])
    _create_adjustment(
        plan=plan,
        adjustment_type=InstallmentAdjustment.AdjustmentType.EARLY_SETTLEMENT,
        amount_delta=amount - planned_remaining,
        effective_date=timezone.localdate(occurred_at),
        item=settlement_item,
        related_transaction=ledger_transaction,
        note=note,
    )
    return plan


@db_transaction.atomic
def start_refund(*, plan: InstallmentPlan) -> InstallmentPlan:
    plan = InstallmentPlan.objects.select_for_update().get(pk=plan.pk)
    if plan.status not in [
        InstallmentPlan.Status.ACTIVE,
        InstallmentPlan.Status.COMPLETED,
        InstallmentPlan.Status.EARLY_SETTLED,
    ]:
        raise ValidationError("当前计划状态不能开始退款处理。")
    plan.status = InstallmentPlan.Status.REFUND_PROCESSING
    plan.save(update_fields=["status", "updated_at"])
    return plan


@db_transaction.atomic
def refund_posted_item(
    *,
    item: InstallmentItem,
    amount: Decimal,
    occurred_at,
    account: Account | None = None,
    note: str = "",
) -> Transaction:
    _validate_money(amount)
    item = (
        InstallmentItem.objects.select_for_update()
        .select_related("plan", "ledger_transaction")
        .get(pk=item.pk)
    )
    plan = InstallmentPlan.objects.select_for_update().get(pk=item.plan_id)
    if plan.status != InstallmentPlan.Status.REFUND_PROCESSING:
        raise ValidationError("必须先将计划置为退款处理中。")
    if item.status != InstallmentItem.Status.POSTED or item.ledger_transaction_id is None:
        raise ValidationError("只能对已入账期次创建实际退款。")
    refund = ledger_services.create_refund(
        original_transaction=item.ledger_transaction,
        amount=amount,
        occurred_at=occurred_at,
        account=account,
        note=note,
    )
    _create_adjustment(
        plan=plan,
        item=item,
        adjustment_type=InstallmentAdjustment.AdjustmentType.REFUND,
        amount_delta=-amount,
        effective_date=timezone.localdate(occurred_at),
        related_transaction=refund,
        note=note,
    )
    return refund


@db_transaction.atomic
def finish_refund(*, plan: InstallmentPlan) -> InstallmentPlan:
    plan = InstallmentPlan.objects.select_for_update().get(pk=plan.pk)
    if plan.status != InstallmentPlan.Status.REFUND_PROCESSING:
        raise ValidationError("该计划不在退款处理中。")
    if plan.items.filter(status=InstallmentItem.Status.PLANNED).exists():
        status = InstallmentPlan.Status.ACTIVE
    elif plan.items.filter(status=InstallmentItem.Status.POSTED).exists():
        status = InstallmentPlan.Status.COMPLETED
    else:
        status = InstallmentPlan.Status.CANCELLED
    plan.status = status
    plan.save(update_fields=["status", "updated_at"])
    return plan
