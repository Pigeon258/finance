from datetime import date, datetime, time
from decimal import Decimal

from django.db.models import Count, Max, Q, QuerySet, Sum
from django.utils import timezone

from apps.accounts.models import Account

from .models import Category, Tag, Transaction, TransactionEntry, TransactionTemplate


def tag_list(*, include_inactive: bool = True) -> QuerySet[Tag]:
    queryset = Tag.objects.annotate(usage_count=Count("transactions", distinct=True))
    if not include_inactive:
        queryset = queryset.filter(is_active=True)
    return queryset.order_by("applies_to", "name", "id")


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
    return (totals["expenses"] or Decimal("0.00")) - (totals["refunds"] or Decimal("0.00"))


def transaction_list(*, filters: dict | None = None) -> QuerySet[Transaction]:
    filters = filters or {}
    queryset = Transaction.objects.select_related("category", "merchant").prefetch_related(
        "entries__account", "tags"
    )
    if filters.get("date_from"):
        queryset = queryset.filter(occurred_at__date__gte=filters["date_from"])
    if filters.get("date_to"):
        queryset = queryset.filter(occurred_at__date__lte=filters["date_to"])
    if filters.get("transaction_type"):
        queryset = queryset.filter(transaction_type=filters["transaction_type"])
    if filters.get("account"):
        queryset = queryset.filter(entries__account=filters["account"])
    if filters.get("category"):
        queryset = queryset.filter(category=filters["category"])
    if filters.get("amount_min") is not None:
        queryset = queryset.filter(amount__gte=filters["amount_min"])
    if filters.get("amount_max") is not None:
        queryset = queryset.filter(amount__lte=filters["amount_max"])
    if filters.get("keyword"):
        keyword = filters["keyword"]
        queryset = queryset.filter(
            Q(counterparty__icontains=keyword)
            | Q(item_name__icontains=keyword)
            | Q(note__icontains=keyword)
            | Q(merchant__name__icontains=keyword)
            | Q(tags__name__icontains=keyword)
        )
    return queryset.distinct().order_by("-occurred_at", "-id")


def transaction_detail(*, transaction_id: int) -> Transaction:
    return (
        Transaction.objects.select_related("category", "merchant", "related_transaction")
        .prefetch_related("entries__account", "tags", "related_transactions")
        .get(pk=transaction_id)
    )


def recent_accounts(*, balance_nature: str, limit: int = 5) -> QuerySet[Account]:
    return (
        Account.objects.filter(
            is_active=True,
            balance_nature=balance_nature,
            transaction_entries__transaction__status=Transaction.Status.ACTIVE,
        )
        .annotate(last_used=Max("transaction_entries__transaction__occurred_at"))
        .order_by("-last_used", "sort_order", "id")[:limit]
    )


def transaction_template_list(*, include_inactive: bool = True) -> QuerySet[TransactionTemplate]:
    queryset = TransactionTemplate.objects.select_related(
        "primary_account", "secondary_account", "category"
    )
    if not include_inactive:
        queryset = queryset.filter(is_active=True)
    return queryset.order_by("sort_order", "id")


def refunded_amount(*, original_transaction: Transaction) -> Decimal:
    return Transaction.objects.filter(
        related_transaction=original_transaction,
        transaction_type=Transaction.TransactionType.REFUND,
        status=Transaction.Status.ACTIVE,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")


def refundable_remaining(*, original_transaction: Transaction) -> Decimal:
    if (
        original_transaction.transaction_type != Transaction.TransactionType.EXPENSE
        or original_transaction.status != Transaction.Status.ACTIVE
    ):
        return Decimal("0.00")
    return max(
        original_transaction.amount - refunded_amount(original_transaction=original_transaction),
        Decimal("0.00"),
    )


def large_flexible_expenses(*, month: date, threshold: Decimal) -> QuerySet[Transaction]:
    month = month.replace(day=1)
    return (
        active_transactions()
        .filter(
            transaction_type=Transaction.TransactionType.EXPENSE,
            occurred_at__year=month.year,
            occurred_at__month=month.month,
            category__necessity=Category.Necessity.FLEXIBLE,
            amount__gt=threshold,
        )
        .order_by("occurred_at", "id")
    )


def reporting_transactions(
    *,
    date_from: date,
    date_to: date,
    transaction_type: str | None = None,
    account_id: int | None = None,
    category_id: int | None = None,
) -> QuerySet[Transaction]:
    queryset = active_transactions().filter(
        occurred_at__date__gte=date_from,
        occurred_at__date__lte=date_to,
    )
    if transaction_type:
        queryset = queryset.filter(transaction_type=transaction_type)
    if account_id:
        queryset = queryset.filter(entries__account_id=account_id)
    if category_id:
        queryset = queryset.filter(category_id=category_id)
    return queryset


def net_funds_series(*, dates: list[date]) -> tuple[tuple[date, Decimal], ...]:
    """Return end-of-day net funds with two bounded queries, regardless of date count."""
    if not dates:
        return ()
    ordered_dates = sorted(set(dates))
    accounts = list(Account.objects.all())
    balances = {account.id: account.initial_balance for account in accounts}
    last_end = timezone.make_aware(
        datetime.combine(ordered_dates[-1], time.max), timezone.get_current_timezone()
    )
    entries = list(
        TransactionEntry.objects.filter(
            transaction__status__in=[Transaction.Status.ACTIVE, Transaction.Status.REVERSED],
            transaction__occurred_at__lte=last_end,
        )
        .select_related("transaction")
        .order_by("transaction__occurred_at", "id")
    )
    result: list[tuple[date, Decimal]] = []
    entry_index = 0
    for point_date in ordered_dates:
        point_end = timezone.make_aware(
            datetime.combine(point_date, time.max), timezone.get_current_timezone()
        )
        while (
            entry_index < len(entries) and entries[entry_index].transaction.occurred_at <= point_end
        ):
            entry = entries[entry_index]
            balances[entry.account_id] += entry.balance_delta
            entry_index += 1
        assets = sum(
            (
                balances[account.id]
                for account in accounts
                if account.balance_nature == Account.BalanceNature.ASSET
            ),
            Decimal("0.00"),
        )
        liabilities = sum(
            (
                max(balances[account.id], Decimal("0.00"))
                for account in accounts
                if account.balance_nature == Account.BalanceNature.LIABILITY
            ),
            Decimal("0.00"),
        )
        result.append((point_date, assets - liabilities))
    return tuple(result)
