from datetime import date
from decimal import Decimal

from django.db.models import Q, QuerySet, Sum

from apps.accounts.models import Account

from .models import Category, Transaction, TransactionEntry


def category_list(*, include_inactive: bool = True) -> QuerySet[Category]:
    queryset = Category.objects.all()
    if not include_inactive:
        queryset = queryset.filter(is_active=True)
    return queryset.order_by("category_type", "sort_order", "id")


def balance_entries(*, account: Account, as_of=None) -> QuerySet[TransactionEntry]:
    queryset = TransactionEntry.objects.filter(
        account=account,
        transaction__status__in=[Transaction.Status.ACTIVE, Transaction.Status.REVERSED],
    )
    if as_of is not None:
        queryset = queryset.filter(transaction__occurred_at__lte=as_of)
    return queryset


def account_balance(*, account: Account, as_of=None) -> Decimal:
    entries_total = balance_entries(account=account, as_of=as_of).aggregate(
        total=Sum("balance_delta")
    )["total"] or Decimal("0.00")
    return account.initial_balance + entries_total


def liquid_assets(*, as_of=None) -> Decimal:
    return sum(
        (
            account_balance(account=account, as_of=as_of)
            for account in Account.objects.filter(balance_nature=Account.BalanceNature.ASSET)
        ),
        Decimal("0.00"),
    )


def current_liabilities(*, as_of=None) -> Decimal:
    return sum(
        (
            max(account_balance(account=account, as_of=as_of), Decimal("0.00"))
            for account in Account.objects.filter(balance_nature=Account.BalanceNature.LIABILITY)
        ),
        Decimal("0.00"),
    )


def current_net_funds(*, as_of=None) -> Decimal:
    return liquid_assets(as_of=as_of) - current_liabilities(as_of=as_of)


def active_transactions() -> QuerySet[Transaction]:
    return Transaction.objects.filter(status=Transaction.Status.ACTIVE)


def monthly_income(*, month: date, category: Category | None = None) -> Decimal:
    queryset = active_transactions().filter(
        transaction_type=Transaction.TransactionType.INCOME,
        budget_month=month.replace(day=1),
    )
    if category is not None:
        queryset = queryset.filter(category=category)
    return queryset.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")


def monthly_net_expense(*, month: date, category: Category | None = None) -> Decimal:
    queryset = active_transactions().filter(
        transaction_type__in=[
            Transaction.TransactionType.EXPENSE,
            Transaction.TransactionType.REFUND,
        ],
        budget_month=month.replace(day=1),
    )
    if category is not None:
        queryset = queryset.filter(category=category)
    totals = queryset.aggregate(
        expenses=Sum("amount", filter=Q(transaction_type=Transaction.TransactionType.EXPENSE)),
        refunds=Sum("amount", filter=Q(transaction_type=Transaction.TransactionType.REFUND)),
    )
    return (totals["expenses"] or Decimal("0.00")) - (
        totals["refunds"] or Decimal("0.00")
    )
