from datetime import date
from decimal import Decimal

from django.db.models import QuerySet, Sum
from django.utils import timezone

from apps.ledger import selectors as ledger_selectors

from .models import WealthAccount, WealthFlow

ZERO = Decimal("0.00")


def account_list(*, include_inactive: bool = True) -> QuerySet[WealthAccount]:
    queryset = WealthAccount.objects.select_related("core_account")
    if not include_inactive:
        queryset = queryset.filter(is_active=True)
    return queryset.order_by("sort_order", "id")


def flows(*, account: WealthAccount | None = None) -> QuerySet[WealthFlow]:
    queryset = WealthFlow.objects.select_related("wealth_account", "related_transaction")
    if account is not None:
        queryset = queryset.filter(wealth_account=account)
    return queryset


def total_value() -> Decimal:
    return (
        WealthAccount.objects.filter(is_active=True).aggregate(total=Sum("current_value"))["total"]
        or ZERO
    )


def total_principal() -> Decimal:
    total = ZERO
    for account in account_list():
        total += ledger_selectors.account_balance(account=account.core_account)
    return total


def month_income(*, month: date | None = None) -> Decimal:
    month = (month or timezone.localdate()).replace(day=1)
    if month.month == 12:
        next_month = date(month.year + 1, 1, 1)
    else:
        next_month = date(month.year, month.month + 1, 1)
    return (
        WealthFlow.objects.filter(
            flow_type=WealthFlow.FlowType.INCOME,
            occurred_on__gte=month,
            occurred_on__lt=next_month,
        ).aggregate(total=Sum("amount"))["total"]
        or ZERO
    )
