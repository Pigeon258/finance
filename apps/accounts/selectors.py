from django.db.models import QuerySet

from .models import Account, AccountReconciliation


def account_list(*, include_inactive: bool = True) -> QuerySet[Account]:
    queryset = Account.objects.all()
    if not include_inactive:
        queryset = queryset.filter(is_active=True)
    return queryset.order_by("sort_order", "id")


def reconciliation_list(*, account: Account) -> QuerySet[AccountReconciliation]:
    return AccountReconciliation.objects.filter(account=account).order_by("-checked_at", "-id")
