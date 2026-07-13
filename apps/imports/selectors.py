from django.db.models import QuerySet

from .models import ImportBatch, ImportRecord


def batch_list() -> QuerySet[ImportBatch]:
    return ImportBatch.objects.all().order_by("-uploaded_at", "-id")


def batch_detail(*, batch_id: int) -> ImportBatch:
    return ImportBatch.objects.get(pk=batch_id)


def record_list(*, batch: ImportBatch) -> QuerySet[ImportRecord]:
    return batch.records.select_related(
        "mapped_account", "suggested_category", "selected_category"
    ).order_by("row_number", "id")
