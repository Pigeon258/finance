from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import DatabaseError, connection
from django.db.models import Count, Q, Sum

from apps.accounts.models import Account, AccountReconciliation
from apps.budgets.models import MonthlyBudget, PlannedCashFlowOccurrence
from apps.credit.models import CreditCardProfile
from apps.imports.models import ImportRecord
from apps.installments.models import InstallmentItem
from apps.ledger.models import Transaction


@dataclass(frozen=True)
class IntegrityIssue:
    code: str
    message: str


def financial_integrity_issues() -> tuple[IntegrityIssue, ...]:
    issues: list[IntegrityIssue] = []
    transactions = Transaction.objects.select_related(
        "category", "related_transaction"
    ).prefetch_related("entries__account")
    for ledger_transaction in transactions:
        try:
            ledger_transaction.full_clean()
        except ValidationError as error:
            issues.append(
                IntegrityIssue("TRANSACTION_INVALID", f"交易 {ledger_transaction.pk}：{error}")
            )
            continue
        entries = list(ledger_transaction.entries.all())
        expected_count = (
            2 if ledger_transaction.transaction_type == Transaction.TransactionType.TRANSFER else 1
        )
        if len(entries) != expected_count:
            detail = (
                f"交易 {ledger_transaction.pk} 应有 {expected_count} 条账本条目，"
                f"实际为 {len(entries)} 条。"
            )
            issues.append(
                IntegrityIssue(
                    "ENTRY_COUNT",
                    detail,
                )
            )
            continue
        if any(abs(entry.balance_delta) != ledger_transaction.amount for entry in entries):
            issues.append(
                IntegrityIssue(
                    "ENTRY_AMOUNT", f"交易 {ledger_transaction.pk} 的条目金额与交易金额不一致。"
                )
            )

        if ledger_transaction.transaction_type == Transaction.TransactionType.INCOME:
            if (
                entries[0].account.balance_nature != Account.BalanceNature.ASSET
                or entries[0].balance_delta <= 0
            ):
                issues.append(
                    IntegrityIssue(
                        "INCOME_SIGN", f"收入交易 {ledger_transaction.pk} 的资产变化无效。"
                    )
                )
        elif ledger_transaction.transaction_type == Transaction.TransactionType.EXPENSE:
            entry = entries[0]
            expected_positive = entry.account.balance_nature == Account.BalanceNature.LIABILITY
            if (entry.balance_delta > 0) != expected_positive:
                issues.append(
                    IntegrityIssue(
                        "EXPENSE_SIGN", f"支出交易 {ledger_transaction.pk} 的余额方向无效。"
                    )
                )
        elif ledger_transaction.transaction_type == Transaction.TransactionType.REFUND:
            entry = entries[0]
            expected_positive = entry.account.balance_nature == Account.BalanceNature.ASSET
            if (entry.balance_delta > 0) != expected_positive:
                issues.append(
                    IntegrityIssue(
                        "REFUND_SIGN", f"退款交易 {ledger_transaction.pk} 的余额方向无效。"
                    )
                )
        elif ledger_transaction.transaction_type == Transaction.TransactionType.TRANSFER:
            asset_entries = [
                entry
                for entry in entries
                if entry.account.balance_nature == Account.BalanceNature.ASSET
            ]
            liability_entries = [
                entry
                for entry in entries
                if entry.account.balance_nature == Account.BalanceNature.LIABILITY
            ]
            regular_transfer = len(asset_entries) == 2 and {
                entry.balance_delta > 0 for entry in entries
            } == {
                True,
                False,
            }
            repayment = (
                len(asset_entries) == 1
                and len(liability_entries) == 1
                and all(entry.balance_delta < 0 for entry in entries)
            )
            if not regular_transfer and not repayment:
                issues.append(
                    IntegrityIssue(
                        "TRANSFER_SIGN", f"转账交易 {ledger_transaction.pk} 的账户性质或方向无效。"
                    )
                )

    refunds = (
        Transaction.objects.filter(
            transaction_type=Transaction.TransactionType.REFUND,
            status=Transaction.Status.ACTIVE,
        )
        .values("related_transaction_id")
        .annotate(total=Sum("amount"))
    )
    originals = {
        row.pk: row.amount
        for row in Transaction.objects.filter(
            pk__in=[row["related_transaction_id"] for row in refunds]
        )
    }
    for row in refunds:
        original_amount = originals.get(row["related_transaction_id"], Decimal("0.00"))
        if row["total"] > original_amount:
            issues.append(
                IntegrityIssue(
                    "REFUND_LIMIT",
                    f"原交易 {row['related_transaction_id']} 的有效退款累计超过原金额。",
                )
            )

    invalid_posted = InstallmentItem.objects.filter(
        Q(ledger_transaction__isnull=True)
        | Q(actual_amount__isnull=True)
        | Q(posted_at__isnull=True),
        status=InstallmentItem.Status.POSTED,
    )
    invalid_unposted = InstallmentItem.objects.exclude(status=InstallmentItem.Status.POSTED).filter(
        Q(ledger_transaction__isnull=False)
        | Q(actual_amount__isnull=False)
        | Q(billing_cycle__isnull=False)
        | Q(posted_at__isnull=False)
    )
    if invalid_posted.exists() or invalid_unposted.exists():
        issues.append(IntegrityIssue("INSTALLMENT_LINK", "分期期次状态与正式交易关联不一致。"))

    if any(month.day != 1 for month in MonthlyBudget.objects.values_list("month", flat=True)):
        issues.append(IntegrityIssue("BUDGET_MONTH", "存在不是月首日的预算月份。"))

    invalid_occurrences = PlannedCashFlowOccurrence.objects.filter(
        Q(linked_transaction__isnull=True) | Q(confirmed_at__isnull=True),
        status=PlannedCashFlowOccurrence.Status.CONFIRMED,
    ) | PlannedCashFlowOccurrence.objects.exclude(
        status=PlannedCashFlowOccurrence.Status.CONFIRMED
    ).filter(Q(linked_transaction__isnull=False) | Q(confirmed_at__isnull=False))
    if invalid_occurrences.exists():
        issues.append(
            IntegrityIssue("PLANNED_CASH_FLOW_LINK", "计划现金流状态与正式交易关联不一致。")
        )

    if CreditCardProfile.objects.filter(is_active=True).count() > 1:
        issues.append(IntegrityIssue("ACTIVE_CREDIT_CARD", "存在多个有效信用卡配置。"))

    adjustment_ids = AccountReconciliation.objects.exclude(
        adjustment_transaction_id__isnull=True
    ).values_list("adjustment_transaction_id", flat=True)
    existing_adjustment_ids = set(
        Transaction.objects.filter(pk__in=adjustment_ids).values_list("pk", flat=True)
    )
    if any(transaction_id not in existing_adjustment_ids for transaction_id in adjustment_ids):
        issues.append(IntegrityIssue("RECONCILIATION_LINK", "余额核对记录关联了不存在的调整交易。"))

    duplicate_import_links = (
        ImportRecord.objects.filter(imported_transaction__isnull=False)
        .values("imported_transaction_id")
        .annotate(count=Count("id"))
        .filter(count__gt=1)
    )
    duplicate_external_keys = (
        ImportRecord.objects.filter(status=ImportRecord.Status.IMPORTED)
        .exclude(source_external_key="")
        .values("source_external_key")
        .annotate(count=Count("id"))
        .filter(count__gt=1)
    )
    if duplicate_import_links.exists() or duplicate_external_keys.exists():
        issues.append(
            IntegrityIssue("IMPORT_DUPLICATE", "导入记录存在重复正式交易关联或外部流水号。")
        )

    try:
        connection.check_constraints()
    except DatabaseError:
        issues.append(IntegrityIssue("DANGLING_FOREIGN_KEY", "数据库中存在悬空外键。"))

    return tuple(issues)


def assert_financial_integrity() -> None:
    issues = financial_integrity_issues()
    if issues:
        raise ValidationError([issue.message for issue in issues])
