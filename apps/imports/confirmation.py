import logging
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.ledger import services as ledger_services
from apps.ledger.models import Transaction

from .models import ImportBatch, ImportDuplicateCandidate, ImportRecord

logger = logging.getLogger(__name__)
MAX_CONFIRM_RECORDS = 500


@dataclass(frozen=True)
class ConfirmationResult:
    imported_count: int
    ignored_count: int
    skipped_count: int


def _channel(batch: ImportBatch) -> str:
    channel = {
        ImportBatch.Source.ALIPAY: Transaction.Channel.ALIPAY,
        ImportBatch.Source.WECHAT: Transaction.Channel.WECHAT,
    }.get(batch.source)
    if channel is None:
        raise ValidationError("导入批次来源尚未识别。")
    return channel


def _selected_candidate(record: ImportRecord) -> ImportDuplicateCandidate | None:
    return (
        record.duplicate_candidates.select_for_update()
        .select_related("transaction")
        .filter(is_selected=True)
        .first()
    )


def _exact_external_duplicate(record: ImportRecord) -> bool:
    if not record.source_external_key:
        return False
    return (
        ImportRecord.objects.select_for_update()
        .filter(
            source_external_key=record.source_external_key,
            status=ImportRecord.Status.IMPORTED,
        )
        .exclude(pk=record.pk)
        .exists()
    )


def _create_ledger_transaction(record: ImportRecord, batch: ImportBatch) -> Transaction:
    common = {
        "account": record.mapped_account,
        "category": record.selected_category,
        "amount": record.amount,
        "occurred_at": record.occurred_at,
        "channel": _channel(batch),
        "counterparty": record.counterparty_raw,
        "note": record.sanitized_raw_data.get("note", ""),
        "source": Transaction.Source.IMPORT,
    }
    if record.candidate_transaction_type == ImportRecord.CandidateTransactionType.INCOME:
        return ledger_services.create_income(**common)
    if record.candidate_transaction_type == ImportRecord.CandidateTransactionType.EXPENSE:
        return ledger_services.create_expense(**common)
    if record.candidate_transaction_type == ImportRecord.CandidateTransactionType.REFUND:
        candidate = _selected_candidate(record)
        if (
            candidate is None
            or candidate.match_kind != ImportDuplicateCandidate.MatchKind.REFUND_CANDIDATE
        ):
            raise ValidationError("退款记录必须选择一笔合法的原支出。")
        return ledger_services.create_refund(
            original_transaction=candidate.transaction,
            account=record.mapped_account,
            amount=record.amount,
            occurred_at=record.occurred_at,
            channel=_channel(batch),
            note=record.sanitized_raw_data.get("note", ""),
            source=Transaction.Source.IMPORT,
        )
    raise ValidationError("该候选交易类型不能直接导入。")


def _resolve_duplicate(record: ImportRecord, batch: ImportBatch) -> tuple[str, Transaction | None]:
    resolution = record.duplicate_resolution
    candidate = _selected_candidate(record)
    if not resolution:
        raise ValidationError("疑似重复记录必须先选择处理方式。")
    if resolution == ImportRecord.DuplicateResolution.KEEP_MANUAL:
        if candidate is None:
            raise ValidationError("保留现有记录前必须选择重复候选。")
        return ImportRecord.Status.IGNORED, None
    if resolution == ImportRecord.DuplicateResolution.KEEP_BOTH:
        return ImportRecord.Status.IMPORTED, _create_ledger_transaction(record, batch)
    if candidate is None or candidate.match_kind != ImportDuplicateCandidate.MatchKind.FUZZY:
        raise ValidationError("替换或合并只能选择疑似重复的手工交易。")
    if candidate.transaction.source != Transaction.Source.MANUAL:
        raise ValidationError("只能替换或合并手工交易。")
    if resolution == ImportRecord.DuplicateResolution.REPLACE_MANUAL:
        ledger_services.void_transaction(
            ledger_transaction=candidate.transaction,
            reason=f"由导入批次 {batch.pk} 的第 {record.row_number} 行替换",
        )
        return ImportRecord.Status.IMPORTED, _create_ledger_transaction(record, batch)
    if resolution == ImportRecord.DuplicateResolution.MERGE:
        if (
            ImportRecord.objects.filter(imported_transaction=candidate.transaction)
            .exclude(pk=record.pk)
            .exists()
        ):
            raise ValidationError("所选手工交易已经合并过其他导入记录。")
        merged = ledger_services.merge_import_information(
            ledger_transaction=candidate.transaction,
            channel=_channel(batch),
            counterparty=record.counterparty_raw,
        )
        return ImportRecord.Status.IMPORTED, merged
    raise ValidationError("重复处理方式无效。")


def refresh_batch_counts(batch: ImportBatch) -> None:
    batch.imported_count = batch.records.filter(status=ImportRecord.Status.IMPORTED).count()
    batch.ignored_count = batch.records.filter(status=ImportRecord.Status.IGNORED).count()
    batch.failed_count = batch.records.filter(status=ImportRecord.Status.FAILED).count()
    unresolved = batch.records.filter(
        status__in=[
            ImportRecord.Status.PENDING,
            ImportRecord.Status.READY,
            ImportRecord.Status.DUPLICATE_SUSPECTED,
        ]
    ).exists()
    batch.status = (
        ImportBatch.Status.PARTIALLY_IMPORTED if unresolved else ImportBatch.Status.COMPLETED
    )
    batch.save(update_fields=["imported_count", "ignored_count", "failed_count", "status"])


@transaction.atomic
def confirm_records(*, batch: ImportBatch, record_ids: list[int]) -> ConfirmationResult:
    unique_ids = list(dict.fromkeys(record_ids))
    if not unique_ids:
        raise ValidationError("请至少选择一条记录。")
    if len(unique_ids) > MAX_CONFIRM_RECORDS:
        raise ValidationError("每次最多确认 500 条记录。")
    locked_batch = ImportBatch.objects.select_for_update().get(pk=batch.pk)
    if locked_batch.status not in {
        ImportBatch.Status.WAITING_CONFIRMATION,
        ImportBatch.Status.PARTIALLY_IMPORTED,
        ImportBatch.Status.COMPLETED,
    }:
        raise ValidationError("当前批次不能确认入账。")
    records = list(
        ImportRecord.objects.select_for_update()
        .select_related("mapped_account", "selected_category", "batch")
        .filter(batch=locked_batch, id__in=unique_ids)
        .order_by("id")
    )
    if len(records) != len(unique_ids):
        raise ValidationError("选择中包含不属于该批次的记录。")

    imported = ignored = skipped = 0
    for record in records:
        if record.status in {ImportRecord.Status.IMPORTED, ImportRecord.Status.IGNORED}:
            skipped += 1
            continue
        if record.status not in {
            ImportRecord.Status.READY,
            ImportRecord.Status.DUPLICATE_SUSPECTED,
        }:
            raise ValidationError(f"第 {record.row_number} 行尚未满足确认条件。")
        if _exact_external_duplicate(record) and not (
            record.status == ImportRecord.Status.DUPLICATE_SUSPECTED
            and record.duplicate_resolution == ImportRecord.DuplicateResolution.KEEP_MANUAL
        ):
            raise ValidationError(f"第 {record.row_number} 行的外部流水号已经入账。")
        if not record.mapped_account_id or not record.selected_category_id:
            raise ValidationError(f"第 {record.row_number} 行缺少账户或分类。")

        if record.status == ImportRecord.Status.DUPLICATE_SUSPECTED:
            target_status, ledger_transaction = _resolve_duplicate(record, locked_batch)
        else:
            target_status = ImportRecord.Status.IMPORTED
            ledger_transaction = _create_ledger_transaction(record, locked_batch)
        record.status = target_status
        record.imported_transaction = ledger_transaction
        try:
            with transaction.atomic():
                record.save(update_fields=["status", "imported_transaction"])
        except IntegrityError as error:
            raise ValidationError("该记录已被其他确认操作入账，请刷新后重试。") from error
        if target_status == ImportRecord.Status.IMPORTED:
            imported += 1
        else:
            ignored += 1
    refresh_batch_counts(locked_batch)
    logger.info(
        "Import batch %s confirmed: imported=%s ignored=%s skipped=%s",
        locked_batch.pk,
        imported,
        ignored,
        skipped,
    )
    return ConfirmationResult(imported, ignored, skipped)
