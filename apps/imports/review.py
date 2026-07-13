from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import Account
from apps.ledger.models import Category, Transaction

from .models import (
    ImportAccountRule,
    ImportBatch,
    ImportDuplicateCandidate,
    ImportRecord,
    MerchantCategoryRule,
)
from .normalization import normalize_key


def duplicate_level(score: int) -> str:
    if score >= 85:
        return "HIGH"
    if score >= 70:
        return "POSSIBLE"
    return "NONE"


def _matches(value: str, *, pattern: str, match_kind: str) -> bool:
    if match_kind == "EXACT":
        return value == pattern
    return bool(pattern) and pattern in value


def suggest_account(record: ImportRecord) -> Account | None:
    payment_method = normalize_key(record.payment_method_raw)
    rules = ImportAccountRule.objects.filter(is_active=True).filter(
        Q(source=record.batch.source) | Q(source=ImportBatch.Source.UNKNOWN)
    )
    for match_kind in (ImportAccountRule.MatchKind.EXACT, ImportAccountRule.MatchKind.CONTAINS):
        for rule in rules.filter(match_kind=match_kind).select_related("account"):
            if rule.account.is_active and _matches(
                payment_method, pattern=rule.pattern, match_kind=rule.match_kind
            ):
                return rule.account

    aliases = (
        (("信用卡", "贷记卡"), Account.AccountType.CREDIT_CARD),
        (("银行卡", "储蓄卡", "借记卡", "银行"), Account.AccountType.BANK),
        (("零钱", "微信零钱"), Account.AccountType.WECHAT),
        (("支付宝余额", "余额宝", "花呗"), Account.AccountType.ALIPAY),
    )
    for terms, account_type in aliases:
        if any(term in payment_method for term in terms):
            account = Account.objects.filter(is_active=True, account_type=account_type).first()
            if account is not None:
                return account
    source_type = {
        ImportBatch.Source.ALIPAY: Account.AccountType.ALIPAY,
        ImportBatch.Source.WECHAT: Account.AccountType.WECHAT,
    }.get(record.batch.source)
    if source_type:
        return Account.objects.filter(is_active=True, account_type=source_type).first()
    return None


def suggest_category(record: ImportRecord) -> Category | None:
    raw = record.sanitized_raw_data
    values = {
        MerchantCategoryRule.MatchTarget.MERCHANT: normalize_key(record.counterparty_raw),
        MerchantCategoryRule.MatchTarget.ITEM: normalize_key(raw.get("item_name", "")),
        MerchantCategoryRule.MatchTarget.BUSINESS_TYPE: normalize_key(raw.get("business_type", "")),
    }
    precedence = (
        (MerchantCategoryRule.MatchTarget.MERCHANT, MerchantCategoryRule.MatchKind.EXACT),
        (MerchantCategoryRule.MatchTarget.MERCHANT, MerchantCategoryRule.MatchKind.CONTAINS),
        (MerchantCategoryRule.MatchTarget.ITEM, MerchantCategoryRule.MatchKind.EXACT),
        (MerchantCategoryRule.MatchTarget.ITEM, MerchantCategoryRule.MatchKind.CONTAINS),
        (MerchantCategoryRule.MatchTarget.BUSINESS_TYPE, MerchantCategoryRule.MatchKind.EXACT),
        (MerchantCategoryRule.MatchTarget.BUSINESS_TYPE, MerchantCategoryRule.MatchKind.CONTAINS),
    )
    rules = MerchantCategoryRule.objects.filter(is_active=True).select_related("category")
    expected_category_type = (
        Category.CategoryType.INCOME
        if record.candidate_transaction_type == ImportRecord.CandidateTransactionType.INCOME
        else Category.CategoryType.EXPENSE
    )
    for target, match_kind in precedence:
        for rule in rules.filter(match_target=target, match_kind=match_kind):
            if (
                rule.category.is_active
                and rule.category.category_type == expected_category_type
                and _matches(values[target], pattern=rule.pattern, match_kind=rule.match_kind)
            ):
                return rule.category
    return None


def _transaction_account_id(ledger_transaction: Transaction) -> int | None:
    entry = next(iter(ledger_transaction.entries.all()), None)
    return entry.account_id if entry else None


def _merchant_score(record: ImportRecord, ledger_transaction: Transaction) -> tuple[int, str]:
    imported = normalize_key(record.counterparty_raw)
    existing = normalize_key(ledger_transaction.counterparty)
    if imported and existing and imported == existing:
        return 10, "交易对方相同"
    if imported and existing and (imported in existing or existing in imported):
        return 6, "交易对方包含匹配"
    return 0, ""


def fuzzy_score(record: ImportRecord, ledger_transaction: Transaction) -> tuple[int, list[str]]:
    score = 55
    reasons = ["金额相同", "交易类型一致"]
    if record.mapped_account_id == _transaction_account_id(ledger_transaction):
        score += 20
        reasons.append("实际账户一致")
    difference = abs(ledger_transaction.occurred_at - record.occurred_at)
    if difference <= timedelta(minutes=5):
        score += 15
        reasons.append("时间差不超过 5 分钟")
    elif difference <= timedelta(hours=1):
        score += 10
        reasons.append("时间差不超过 1 小时")
    elif (
        timezone.localtime(ledger_transaction.occurred_at).date()
        == timezone.localtime(record.occurred_at).date()
    ):
        score += 5
        reasons.append("发生在同一天")
    merchant_score, reason = _merchant_score(record, ledger_transaction)
    score += merchant_score
    if reason:
        reasons.append(reason)
    return min(score, 100), reasons


def _add_candidate(
    *, record: ImportRecord, ledger_transaction: Transaction, match_kind: str, score: int, reasons
) -> None:
    ImportDuplicateCandidate.objects.get_or_create(
        import_record=record,
        transaction=ledger_transaction,
        match_kind=match_kind,
        defaults={"score": score, "reasons": list(reasons)},
    )


def detect_duplicates(record: ImportRecord) -> bool:
    record.duplicate_candidates.filter(is_selected=False).delete()
    has_duplicate = False
    if record.source_external_key:
        previous = (
            ImportRecord.objects.filter(
                source_external_key=record.source_external_key,
                status=ImportRecord.Status.IMPORTED,
                imported_transaction__isnull=False,
            )
            .exclude(pk=record.pk)
            .select_related("imported_transaction")
        )
        for other in previous:
            _add_candidate(
                record=record,
                ledger_transaction=other.imported_transaction,
                match_kind=ImportDuplicateCandidate.MatchKind.EXACT_EXTERNAL_ID,
                score=100,
                reasons=["来源与外部流水号一致"],
            )
            has_duplicate = True
    if record.exact_fingerprint:
        previous = (
            ImportRecord.objects.filter(
                exact_fingerprint=record.exact_fingerprint,
                status=ImportRecord.Status.IMPORTED,
                imported_transaction__isnull=False,
            )
            .exclude(pk=record.pk)
            .select_related("imported_transaction")
        )
        for other in previous:
            _add_candidate(
                record=record,
                ledger_transaction=other.imported_transaction,
                match_kind=ImportDuplicateCandidate.MatchKind.EXACT_FINGERPRINT,
                score=100,
                reasons=["标准指纹一致"],
            )
            has_duplicate = True

    type_map = {
        ImportRecord.CandidateTransactionType.INCOME: Transaction.TransactionType.INCOME,
        ImportRecord.CandidateTransactionType.EXPENSE: Transaction.TransactionType.EXPENSE,
    }
    ledger_type = type_map.get(record.candidate_transaction_type)
    if ledger_type and record.amount is not None and record.occurred_at is not None:
        candidates = (
            Transaction.objects.filter(
                source=Transaction.Source.MANUAL,
                status=Transaction.Status.ACTIVE,
                transaction_type=ledger_type,
                amount=record.amount,
                occurred_at__gte=record.occurred_at - timedelta(hours=24),
                occurred_at__lte=record.occurred_at + timedelta(hours=24),
            )
            .prefetch_related("entries")
            .order_by("id")
        )
        for candidate in candidates:
            score, reasons = fuzzy_score(record, candidate)
            if duplicate_level(score) != "NONE":
                _add_candidate(
                    record=record,
                    ledger_transaction=candidate,
                    match_kind=ImportDuplicateCandidate.MatchKind.FUZZY,
                    score=score,
                    reasons=reasons,
                )
                has_duplicate = True
    return (
        has_duplicate
        or record.duplicate_candidates.filter(
            is_selected=True,
            match_kind__in=[
                ImportDuplicateCandidate.MatchKind.EXACT_EXTERNAL_ID,
                ImportDuplicateCandidate.MatchKind.EXACT_FINGERPRINT,
                ImportDuplicateCandidate.MatchKind.FUZZY,
            ],
        ).exists()
    )


def suggest_refund_candidates(record: ImportRecord) -> bool:
    if record.candidate_transaction_type != ImportRecord.CandidateTransactionType.REFUND:
        return False
    queryset = Transaction.objects.filter(
        transaction_type=Transaction.TransactionType.EXPENSE,
        status=Transaction.Status.ACTIVE,
        amount__gte=record.amount,
        occurred_at__lte=record.occurred_at,
    ).prefetch_related("entries")
    if record.mapped_account_id:
        queryset = queryset.filter(entries__account_id=record.mapped_account_id)
    related_id = record.sanitized_raw_data.get("related_external_id", "")
    if related_id:
        linked_ids = ImportRecord.objects.filter(
            external_transaction_id=related_id,
            status=ImportRecord.Status.IMPORTED,
        ).values_list("imported_transaction_id", flat=True)
        queryset = queryset.filter(Q(id__in=linked_ids) | Q(counterparty=record.counterparty_raw))
    queryset = queryset.order_by("-occurred_at", "-id")[:20]
    found = False
    for candidate in queryset:
        _add_candidate(
            record=record,
            ledger_transaction=candidate,
            match_kind=ImportDuplicateCandidate.MatchKind.REFUND_CANDIDATE,
            score=0,
            reasons=["金额与账户可作为退款原交易候选"],
        )
        found = True
    return found


def _ready_for_import(record: ImportRecord, *, refund_candidates: bool) -> bool:
    if not record.mapped_account_id or record.amount is None or record.occurred_at is None:
        return False
    if record.candidate_transaction_type in {
        ImportRecord.CandidateTransactionType.INCOME,
        ImportRecord.CandidateTransactionType.EXPENSE,
    }:
        return record.selected_category_id is not None
    if record.candidate_transaction_type == ImportRecord.CandidateTransactionType.REFUND:
        return record.selected_category_id is not None and refund_candidates
    return False


@transaction.atomic
def prepare_record_for_review(*, record: ImportRecord) -> ImportRecord:
    if record.status not in {
        ImportRecord.Status.PENDING,
        ImportRecord.Status.READY,
        ImportRecord.Status.DUPLICATE_SUSPECTED,
    }:
        return record
    if record.mapped_account_id is None:
        record.mapped_account = suggest_account(record)
    if (
        record.selected_category_id is None
        or record.selected_category_id == record.suggested_category_id
    ):
        record.suggested_category = suggest_category(record)
        record.selected_category = record.suggested_category
    has_duplicate = detect_duplicates(record)
    refund_candidates = suggest_refund_candidates(record)
    if has_duplicate:
        record.status = ImportRecord.Status.DUPLICATE_SUSPECTED
    elif _ready_for_import(record, refund_candidates=refund_candidates):
        record.status = ImportRecord.Status.READY
    else:
        record.status = ImportRecord.Status.PENDING
    record.save(
        update_fields=["mapped_account", "suggested_category", "selected_category", "status"]
    )
    return record


def prepare_batch_for_review(*, batch: ImportBatch) -> None:
    for record in batch.records.exclude(status=ImportRecord.Status.FAILED).iterator():
        prepare_record_for_review(record=record)


@transaction.atomic
def update_record_review(
    *,
    record: ImportRecord,
    mapped_account: Account | None,
    selected_category: Category | None,
    duplicate_resolution: str,
    selected_candidate: ImportDuplicateCandidate | None,
    save_merchant_rule: bool = False,
) -> ImportRecord:
    locked = ImportRecord.objects.select_for_update().select_related("batch").get(pk=record.pk)
    if locked.status in {ImportRecord.Status.IMPORTED, ImportRecord.Status.IGNORED}:
        raise ValueError("已处理的导入记录不能修改。")
    if selected_candidate is not None and selected_candidate.import_record_id != locked.pk:
        raise ValueError("重复候选不属于当前导入记录。")
    locked.mapped_account = mapped_account
    locked.selected_category = selected_category
    locked.duplicate_resolution = duplicate_resolution
    locked.save(update_fields=["mapped_account", "selected_category", "duplicate_resolution"])
    locked.duplicate_candidates.update(is_selected=False)
    if selected_candidate is not None:
        locked.duplicate_candidates.filter(pk=selected_candidate.pk).update(is_selected=True)
    if save_merchant_rule:
        pattern = normalize_key(locked.counterparty_raw)
        if not pattern or selected_category is None:
            raise ValueError("保存商家分类规则前必须选择分类且交易对方不能为空。")
        MerchantCategoryRule.objects.update_or_create(
            match_target=MerchantCategoryRule.MatchTarget.MERCHANT,
            match_kind=MerchantCategoryRule.MatchKind.EXACT,
            pattern=pattern,
            defaults={
                "name": f"导入：{locked.counterparty_raw}"[:100],
                "category": selected_category,
                "priority": 100,
                "is_active": True,
            },
        )
    return prepare_record_for_review(record=locked)


@transaction.atomic
def bulk_update_records(
    *,
    batch: ImportBatch,
    record_ids: list[int],
    account: Account | None = None,
    category: Category | None = None,
    ignore: bool = False,
) -> int:
    unique_ids = list(dict.fromkeys(record_ids))
    if not unique_ids:
        raise ValueError("请至少选择一条记录。")
    records = list(
        ImportRecord.objects.select_for_update()
        .filter(batch=batch, id__in=unique_ids)
        .order_by("id")
    )
    if len(records) != len(unique_ids):
        raise ValueError("选择中包含不属于该批次的记录。")
    changed = 0
    for record in records:
        if record.status in {ImportRecord.Status.IMPORTED, ImportRecord.Status.IGNORED}:
            continue
        if record.status == ImportRecord.Status.FAILED:
            raise ValueError("解析失败记录不能批量修改。")
        if ignore:
            record.status = ImportRecord.Status.IGNORED
            record.save(update_fields=["status"])
            changed += 1
            continue
        if account is not None:
            record.mapped_account = account
            record.save(update_fields=["mapped_account"])
        if category is not None:
            expected = (
                Category.CategoryType.INCOME
                if record.candidate_transaction_type == ImportRecord.CandidateTransactionType.INCOME
                else Category.CategoryType.EXPENSE
            )
            if category.category_type != expected:
                raise ValueError("所选分类与记录类型不匹配。")
            record.selected_category = category
            record.save(update_fields=["selected_category"])
        prepare_record_for_review(record=record)
        changed += 1
    if ignore:
        from .confirmation import refresh_batch_counts

        refresh_batch_counts(batch)
    return changed


def save_category_rule(*, form):
    return form.save()


def save_account_rule(*, form):
    return form.save()
