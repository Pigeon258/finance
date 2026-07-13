from django.db.models import QuerySet

from .models import ImportAccountRule, ImportBatch, ImportRecord, MerchantCategoryRule


def batch_list() -> QuerySet[ImportBatch]:
    return ImportBatch.objects.all().order_by("-uploaded_at", "-id")


def batch_detail(*, batch_id: int) -> ImportBatch:
    return ImportBatch.objects.get(pk=batch_id)


def record_list(*, batch: ImportBatch) -> QuerySet[ImportRecord]:
    return (
        batch.records.select_related("mapped_account", "suggested_category", "selected_category")
        .prefetch_related("duplicate_candidates")
        .order_by("row_number", "id")
    )


def record_detail(*, batch: ImportBatch, record_id: int) -> ImportRecord:
    return (
        record_list(batch=batch)
        .prefetch_related("duplicate_candidates__transaction")
        .get(pk=record_id)
    )


def merchant_category_rules() -> QuerySet[MerchantCategoryRule]:
    return MerchantCategoryRule.objects.select_related("category").order_by("priority", "id")


def import_account_rules() -> QuerySet[ImportAccountRule]:
    return ImportAccountRule.objects.select_related("account").order_by("priority", "id")
