from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Account


def balance_nature_for(account_type: str) -> str:
    if account_type == Account.AccountType.CREDIT_CARD:
        return Account.BalanceNature.LIABILITY
    return Account.BalanceNature.ASSET


@transaction.atomic
def create_account(
    *,
    name: str,
    account_type: str,
    initial_balance: Decimal,
    is_active: bool,
    sort_order: int,
    opened_at=None,
    note: str = "",
) -> Account:
    account = Account(
        name=name,
        account_type=account_type,
        balance_nature=balance_nature_for(account_type),
        initial_balance=initial_balance,
        is_active=is_active,
        sort_order=sort_order,
        opened_at=opened_at,
        note=note,
    )
    account.full_clean()
    account.save()
    return account


@transaction.atomic
def update_account(
    *,
    account: Account,
    name: str,
    initial_balance: Decimal,
    is_active: bool,
    sort_order: int,
    opened_at=None,
    note: str = "",
) -> Account:
    account.name = name
    account.initial_balance = initial_balance
    account.is_active = is_active
    account.sort_order = sort_order
    account.opened_at = opened_at
    account.note = note
    account.full_clean()
    account.save(
        update_fields=[
            "name",
            "initial_balance",
            "is_active",
            "sort_order",
            "opened_at",
            "note",
            "updated_at",
        ]
    )
    return account


@transaction.atomic
def deactivate_account(*, account: Account) -> Account:
    if not account.is_active:
        raise ValidationError("账户已经停用。")
    account.is_active = False
    account.full_clean()
    account.save(update_fields=["is_active", "updated_at"])
    return account
