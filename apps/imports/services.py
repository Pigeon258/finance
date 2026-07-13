import logging
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .file_safety import (
    PreparedBillFile,
    StoredUpload,
    UnsafeImportFile,
    prepare_bill_file,
    safe_delete,
    store_uploaded_file,
)
from .models import ImportBatch, ImportRecord
from .normalization import normalize_record
from .parsers import detect_parser
from .parsers.base import BillParseError, InvalidRecordError

logger = logging.getLogger(__name__)

BATCH_TRANSITIONS = {
    ImportBatch.Status.UPLOADED: {
        ImportBatch.Status.PARSING,
        ImportBatch.Status.FAILED,
        ImportBatch.Status.CANCELLED,
    },
    ImportBatch.Status.PARSING: {
        ImportBatch.Status.WAITING_CONFIRMATION,
        ImportBatch.Status.FAILED,
        ImportBatch.Status.CANCELLED,
    },
    ImportBatch.Status.WAITING_CONFIRMATION: {
        ImportBatch.Status.PARTIALLY_IMPORTED,
        ImportBatch.Status.COMPLETED,
        ImportBatch.Status.CANCELLED,
    },
    ImportBatch.Status.PARTIALLY_IMPORTED: {
        ImportBatch.Status.COMPLETED,
        ImportBatch.Status.CANCELLED,
    },
}

RECORD_TRANSITIONS = {
    ImportRecord.Status.PENDING: {
        ImportRecord.Status.DUPLICATE_SUSPECTED,
        ImportRecord.Status.READY,
        ImportRecord.Status.IGNORED,
        ImportRecord.Status.FAILED,
    },
    ImportRecord.Status.DUPLICATE_SUSPECTED: {
        ImportRecord.Status.READY,
        ImportRecord.Status.IGNORED,
    },
    ImportRecord.Status.READY: {
        ImportRecord.Status.IMPORTED,
        ImportRecord.Status.IGNORED,
    },
}


@dataclass(frozen=True)
class ProcessUploadResult:
    batch: ImportBatch
    duplicate_file: bool = False


def transition_batch(*, batch: ImportBatch, target: str) -> ImportBatch:
    if target not in BATCH_TRANSITIONS.get(batch.status, set()):
        raise ValidationError("导入批次状态转换无效。")
    batch.status = target
    batch.save(update_fields=["status"])
    return batch


def transition_record(*, record: ImportRecord, target: str) -> ImportRecord:
    if target not in RECORD_TRANSITIONS.get(record.status, set()):
        raise ValidationError("导入记录状态转换无效。")
    record.status = target
    record.save(update_fields=["status"])
    return record


def _successful_duplicate(file_sha256: str) -> ImportBatch | None:
    return (
        ImportBatch.objects.filter(
            file_sha256=file_sha256,
            status__in=[
                ImportBatch.Status.WAITING_CONFIRMATION,
                ImportBatch.Status.PARTIALLY_IMPORTED,
                ImportBatch.Status.COMPLETED,
            ],
        )
        .order_by("id")
        .first()
    )


def _record_model(*, batch: ImportBatch, normalized) -> ImportRecord:
    source_external_key = (
        f"{normalized.source}:{normalized.external_transaction_id}"
        if normalized.external_transaction_id
        else ""
    )
    return ImportRecord(
        batch=batch,
        row_number=normalized.row_number,
        external_transaction_id=normalized.external_transaction_id,
        external_order_id=normalized.external_order_id,
        source_external_key=source_external_key,
        exact_fingerprint=normalized.exact_fingerprint,
        occurred_at=normalized.occurred_at,
        candidate_transaction_type=normalized.candidate_transaction_type,
        amount=normalized.amount,
        counterparty_raw=normalized.display_counterparty,
        payment_method_raw=normalized.normalized_payment_method,
        status=ImportRecord.Status.PENDING,
        review_flags=list(normalized.review_flags),
        sanitized_raw_data=normalized.sanitized_raw_data,
    )


def _safe_error_message(error: Exception) -> str:
    if isinstance(error, (UnsafeImportFile, BillParseError, InvalidRecordError)):
        return str(error)[:500]
    return "解析过程中发生内部错误，未保存原始账单内容。"


def _mark_file_deleted(batch: ImportBatch) -> None:
    batch.temporary_file_path = ""
    batch.file_deleted_at = timezone.now()
    batch.save(update_fields=["temporary_file_path", "file_deleted_at"])


def process_uploaded_bill(uploaded_file) -> ProcessUploadResult:
    stored: StoredUpload = store_uploaded_file(uploaded_file)
    duplicate = _successful_duplicate(stored.file_sha256)
    if duplicate is not None:
        safe_delete(stored.path)
        return ProcessUploadResult(duplicate, duplicate_file=True)

    batch = ImportBatch.objects.create(
        original_filename=stored.original_filename,
        file_sha256=stored.file_sha256,
        temporary_file_path=str(stored.path),
    )
    prepared: PreparedBillFile | None = None
    try:
        transition_batch(batch=batch, target=ImportBatch.Status.PARSING)
        prepared = prepare_bill_file(stored)
        parser = detect_parser(prepared.path)
        batch.source = parser.source
        batch.parser_name = parser.parser_name
        batch.parser_version = parser.parser_version
        batch.save(update_fields=["source", "parser_name", "parser_version"])

        records: list[ImportRecord] = []
        total_count = 0
        failed_count = 0
        for parsed in parser.parse(prepared.path):
            total_count += 1
            if total_count > settings.IMPORT_MAX_RECORDS:
                raise BillParseError("账单记录数超过限制。")
            try:
                normalized = normalize_record(source=parser.source, record=parsed)
                records.append(_record_model(batch=batch, normalized=normalized))
            except InvalidRecordError as error:
                failed_count += 1
                records.append(
                    ImportRecord(
                        batch=batch,
                        row_number=parsed.row_number,
                        status=ImportRecord.Status.FAILED,
                        error_message=_safe_error_message(error),
                    )
                )
        if total_count == 0:
            raise BillParseError("账单没有可解析的交易记录。")

        valid_count = total_count - failed_count
        with transaction.atomic():
            ImportRecord.objects.bulk_create(records, batch_size=1000)
            locked = ImportBatch.objects.select_for_update().get(pk=batch.pk)
            locked.total_count = total_count
            locked.failed_count = failed_count
            locked.parsed_at = timezone.now()
            locked.error_summary = "" if valid_count else "所有账单记录均解析失败。"
            locked.status = (
                ImportBatch.Status.WAITING_CONFIRMATION
                if valid_count
                else ImportBatch.Status.FAILED
            )
            locked.save(
                update_fields=[
                    "total_count",
                    "failed_count",
                    "parsed_at",
                    "error_summary",
                    "status",
                ]
            )
        batch.refresh_from_db()
        logger.info(
            "Import batch %s parsed: source=%s total=%s failed=%s",
            batch.pk,
            batch.source,
            batch.total_count,
            batch.failed_count,
        )
    except Exception as error:
        batch.refresh_from_db()
        if batch.status not in {ImportBatch.Status.FAILED, ImportBatch.Status.CANCELLED}:
            batch.status = ImportBatch.Status.FAILED
            batch.error_summary = _safe_error_message(error)
            batch.parsed_at = timezone.now()
            batch.save(update_fields=["status", "error_summary", "parsed_at"])
        logger.warning("Import batch %s failed: error_type=%s", batch.pk, type(error).__name__)
    finally:
        if prepared is not None:
            for path in prepared.cleanup_paths:
                safe_delete(path)
        safe_delete(stored.path)
        _mark_file_deleted(batch)
    return ProcessUploadResult(batch)


def cleanup_stale_import_files(*, before) -> int:
    cleaned = 0
    queryset = ImportBatch.objects.filter(
        uploaded_at__lt=before,
        file_deleted_at__isnull=True,
    ).exclude(temporary_file_path="")
    for batch in queryset.iterator():
        if safe_delete(Path(batch.temporary_file_path)):
            _mark_file_deleted(batch)
            cleaned += 1
    return cleaned
