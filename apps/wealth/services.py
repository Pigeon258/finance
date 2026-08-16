import ast
import re
from datetime import datetime
from decimal import Decimal
from urllib.request import Request, urlopen

from django.core.exceptions import ValidationError
from django.db import transaction as db_transaction
from django.utils import timezone

from apps.accounts.models import Account
from apps.ledger import selectors as ledger_selectors
from apps.ledger import services as ledger_services
from apps.ledger.models import Category, Transaction

from .models import WealthAccount, WealthFlow

MONEY_QUANTUM = Decimal("0.01")
YUEBAO_FUND_CODE = "000198"
YUEBAO_QUOTE_URL = "https://fund.eastmoney.com/pingzhongdata/000198.js"


def _validate_money(value: Decimal, *, allow_negative: bool = False) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValidationError("理财金额必须使用有限 Decimal。")
    if value != value.quantize(MONEY_QUANTUM):
        raise ValidationError("理财金额必须精确到分。")
    if value < 0 and not allow_negative:
        raise ValidationError("理财金额必须大于或等于零。")
    if value == 0:
        raise ValidationError("理财金额不得为零。")


def _validate_daily_account(account: Account) -> None:
    if not account.is_active or account.account_type == Account.AccountType.WEALTH:
        raise ValidationError("请选择启用的日常账户。")


@db_transaction.atomic
def create_wealth_account(
    *,
    name: str,
    account_type: str,
    institution: str = "",
    fund_code: str = "",
    auto_fetch_enabled: bool = False,
    is_active: bool = True,
    sort_order: int = 0,
    opened_on=None,
    note: str = "",
) -> WealthAccount:
    name = name.strip()
    if not name:
        raise ValidationError("理财账户名称不能为空。")
    core_account = Account.objects.create(
        name=f"[理财]{name}",
        account_type=Account.AccountType.WEALTH,
        balance_nature=Account.BalanceNature.ASSET,
        initial_balance=Decimal("0.00"),
        is_active=is_active,
        sort_order=sort_order,
        note=note,
    )
    wealth_account = WealthAccount(
        name=name,
        account_type=account_type,
        institution=institution,
        core_account=core_account,
        fund_code=fund_code,
        auto_fetch_enabled=auto_fetch_enabled,
        is_active=is_active,
        sort_order=sort_order,
        opened_on=opened_on,
        note=note,
    )
    wealth_account.full_clean()
    wealth_account.save()
    return wealth_account


@db_transaction.atomic
def update_wealth_account(*, account: WealthAccount, **data) -> WealthAccount:
    account.name = data["name"].strip()
    account.account_type = data["account_type"]
    account.institution = data["institution"]
    account.fund_code = data["fund_code"]
    account.auto_fetch_enabled = data["auto_fetch_enabled"]
    account.is_active = data["is_active"]
    account.sort_order = data["sort_order"]
    account.opened_on = data["opened_on"]
    account.note = data["note"]
    account.full_clean()
    account.save()
    account.core_account.name = f"[理财]{account.name}"
    account.core_account.is_active = account.is_active
    account.core_account.save(update_fields=["name", "is_active", "updated_at"])
    return account


@db_transaction.atomic
def transfer_in(
    *,
    wealth_account: WealthAccount,
    source_account: Account,
    amount: Decimal,
    occurred_at: datetime,
    note: str = "",
) -> tuple[Transaction, WealthFlow]:
    _validate_money(amount)
    _validate_daily_account(source_account)
    wealth_account = WealthAccount.objects.select_for_update().get(pk=wealth_account.pk)
    ledger_transaction = ledger_services.create_transfer(
        source_account=source_account,
        destination_account=wealth_account.core_account,
        amount=amount,
        occurred_at=occurred_at,
        channel=Transaction.Channel.OTHER,
        counterparty=wealth_account.name,
        note=note,
    )
    wealth_account.current_value += amount
    wealth_account.valuation_date = timezone.localdate(occurred_at)
    wealth_account.full_clean()
    wealth_account.save(update_fields=["current_value", "valuation_date", "updated_at"])
    flow = WealthFlow.objects.create(
        wealth_account=wealth_account,
        flow_type=WealthFlow.FlowType.TRANSFER_IN,
        amount=amount,
        occurred_on=timezone.localdate(occurred_at),
        related_transaction=ledger_transaction,
        note=note,
    )
    return ledger_transaction, flow


@db_transaction.atomic
def transfer_out(
    *,
    wealth_account: WealthAccount,
    destination_account: Account,
    amount: Decimal,
    occurred_at: datetime,
    note: str = "",
) -> tuple[Transaction, WealthFlow]:
    _validate_money(amount)
    _validate_daily_account(destination_account)
    wealth_account = WealthAccount.objects.select_for_update().get(pk=wealth_account.pk)
    available = ledger_selectors.account_balance(account=wealth_account.core_account)
    if amount > available:
        raise ValidationError("转出金额不能超过理财账户本金余额。")
    if amount > wealth_account.current_value:
        raise ValidationError("转出金额不能超过理财账户当前市值。")
    ledger_transaction = ledger_services.create_transfer(
        source_account=wealth_account.core_account,
        destination_account=destination_account,
        amount=amount,
        occurred_at=occurred_at,
        channel=Transaction.Channel.OTHER,
        counterparty=wealth_account.name,
        note=note,
    )
    wealth_account.current_value -= amount
    wealth_account.valuation_date = timezone.localdate(occurred_at)
    wealth_account.full_clean()
    wealth_account.save(update_fields=["current_value", "valuation_date", "updated_at"])
    flow = WealthFlow.objects.create(
        wealth_account=wealth_account,
        flow_type=WealthFlow.FlowType.TRANSFER_OUT,
        amount=amount,
        occurred_on=timezone.localdate(occurred_at),
        related_transaction=ledger_transaction,
        note=note,
    )
    return ledger_transaction, flow


@db_transaction.atomic
def update_valuation(
    *, wealth_account: WealthAccount, current_value: Decimal, valuation_date
) -> WealthFlow:
    wealth_account = WealthAccount.objects.select_for_update().get(pk=wealth_account.pk)
    _validate_money(current_value, allow_negative=False)
    delta = current_value - wealth_account.current_value
    wealth_account.current_value = current_value
    wealth_account.valuation_date = valuation_date
    wealth_account.full_clean()
    wealth_account.save(update_fields=["current_value", "valuation_date", "updated_at"])
    return WealthFlow.objects.create(
        wealth_account=wealth_account,
        flow_type=WealthFlow.FlowType.VALUATION,
        amount=delta,
        occurred_on=valuation_date,
        note="手工估值调整",
    )


@db_transaction.atomic
def record_income(
    *,
    wealth_account: WealthAccount,
    income_category: Category,
    amount: Decimal,
    occurred_on,
    daily_account: Account | None = None,
    note: str = "",
) -> tuple[WealthFlow, Transaction | None]:
    _validate_money(amount)
    if (
        income_category.category_type != Category.CategoryType.INCOME
        or not income_category.is_active
    ):
        raise ValidationError("请选择启用的收入分类。")
    wealth_account = WealthAccount.objects.select_for_update().get(pk=wealth_account.pk)
    related_transaction = None
    if daily_account is not None:
        _validate_daily_account(daily_account)
        related_transaction = ledger_services.create_income(
            account=daily_account,
            category=income_category,
            amount=amount,
            occurred_at=timezone.make_aware(
                datetime.combine(occurred_on, datetime.min.time()),
                timezone.get_current_timezone(),
            ),
            channel=Transaction.Channel.OTHER,
            counterparty=wealth_account.name,
            note=note,
        )
    else:
        wealth_account.current_value += amount
        wealth_account.valuation_date = occurred_on
        wealth_account.save(update_fields=["current_value", "valuation_date", "updated_at"])
    flow = WealthFlow.objects.create(
        wealth_account=wealth_account,
        flow_type=WealthFlow.FlowType.INCOME,
        amount=amount,
        occurred_on=occurred_on,
        related_transaction=related_transaction,
        note=note,
    )
    return flow, related_transaction


def fetch_yuebao_quote() -> dict[str, Decimal]:
    request = Request(YUEBAO_QUOTE_URL, headers={"User-Agent": "personal-finance/0.2"})
    with urlopen(request, timeout=10) as response:
        content = response.read().decode("utf-8", errors="ignore")

    def latest_series(name: str) -> Decimal:
        match = re.search(rf"var {name}\s*=\s*(\[.*?\]);", content)
        if match is None:
            raise ValidationError(f"余额宝数据缺少 {name}。")
        series = ast.literal_eval(match.group(1))
        if not series:
            raise ValidationError("余额宝收益序列为空。")
        return Decimal(str(series[-1][1]))

    return {
        "seven_day_annual_yield": latest_series("Data_sevenDaysYearIncome"),
        "per_ten_thousand_income": latest_series("Data_millionCopiesIncome"),
    }


@db_transaction.atomic
def sync_yuebao(*, wealth_account: WealthAccount) -> WealthAccount:
    if wealth_account.fund_code != YUEBAO_FUND_CODE:
        raise ValidationError("仅余额宝（000198）支持自动同步。")
    quote = fetch_yuebao_quote()
    wealth_account = WealthAccount.objects.select_for_update().get(pk=wealth_account.pk)
    wealth_account.seven_day_annual_yield = quote["seven_day_annual_yield"]
    wealth_account.per_ten_thousand_income = quote["per_ten_thousand_income"]
    wealth_account.last_sync_at = timezone.now()
    wealth_account.save(
        update_fields=[
            "seven_day_annual_yield",
            "per_ten_thousand_income",
            "last_sync_at",
            "updated_at",
        ]
    )
    return wealth_account
