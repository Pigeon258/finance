import io
import logging
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook

from apps.imports import services
from apps.imports.models import ImportBatch, ImportRecord
from apps.ledger.models import Transaction

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def import_tmp(settings, tmp_path):
    settings.IMPORT_TMP_DIR = tmp_path / "imports"


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_superuser(
        username="owner", password="correct horse battery staple"
    )


@pytest.fixture
def authenticated_client(client, owner):
    client.force_login(owner)
    return client


def _fixture_upload(relative_path: str):
    path = FIXTURES / relative_path
    return SimpleUploadedFile(path.name, path.read_bytes(), content_type="text/csv")


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("fixture", "source", "count"),
    [("alipay/v1/normal.csv", "ALIPAY", 2), ("wechat/v1/normal.csv", "WECHAT", 2)],
)
def test_complete_parse_chain_is_isolated_and_deletes_original(fixture, source, count):
    before = Transaction.objects.count()
    result = services.process_uploaded_bill(_fixture_upload(fixture))
    batch = result.batch

    assert batch.status == ImportBatch.Status.WAITING_CONFIRMATION
    assert batch.source == source
    assert batch.total_count == count
    assert batch.file_deleted_at is not None
    assert batch.temporary_file_path == ""
    assert batch.records.filter(status=ImportRecord.Status.PENDING).count() == count
    assert all(record.amount.as_tuple().exponent == -2 for record in batch.records.all())
    assert Transaction.objects.count() == before


@pytest.mark.django_db
def test_same_successful_file_reuses_batch_without_records_or_transactions():
    first = services.process_uploaded_bill(_fixture_upload("alipay/v1/normal.csv"))
    second = services.process_uploaded_bill(_fixture_upload("alipay/v1/normal.csv"))
    assert second.duplicate_file is True
    assert second.batch.id == first.batch.id
    assert ImportBatch.objects.count() == 1
    assert ImportRecord.objects.count() == 2
    assert Transaction.objects.count() == 0


@pytest.mark.django_db
def test_failed_parse_deletes_file_and_logs_no_raw_row(caplog):
    caplog.set_level(logging.INFO)
    upload = _fixture_upload("alipay/v1/missing-column.csv")
    result = services.process_uploaded_bill(upload)
    result.batch.refresh_from_db()
    assert result.batch.status == ImportBatch.Status.FAILED
    assert result.batch.file_deleted_at is not None
    assert result.batch.temporary_file_path == ""
    assert "202607010001" not in caplog.text
    assert "示例书店" not in caplog.text
    assert Transaction.objects.count() == 0


@pytest.mark.django_db
def test_invalid_amount_becomes_failed_record_without_financial_fact():
    result = services.process_uploaded_bill(_fixture_upload("alipay/v1/invalid-amount.csv"))
    record = result.batch.records.get()
    assert result.batch.status == ImportBatch.Status.FAILED
    assert result.batch.failed_count == 1
    assert record.status == ImportRecord.Status.FAILED
    assert record.amount is None
    assert record.sanitized_raw_data == {}
    assert Transaction.objects.count() == 0


@pytest.mark.django_db
def test_unknown_status_is_pending_with_review_flag():
    result = services.process_uploaded_bill(_fixture_upload("wechat/v1/unknown-status.csv"))
    record = result.batch.records.get()
    assert result.batch.status == ImportBatch.Status.WAITING_CONFIRMATION
    assert "UNKNOWN_STATUS" in record.review_flags
    assert record.sanitized_raw_data["canonical_status"] == "UNKNOWN"


def _xlsx_upload():
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["微信支付账单明细"])
    sheet.append(
        [
            "交易时间",
            "交易类型",
            "交易对方",
            "商品",
            "收/支",
            "金额(元)",
            "支付方式",
            "当前状态",
            "交易单号",
            "商户单号",
            "备注",
        ]
    )
    sheet.append(
        [
            "2026-07-03 12:30:00",
            "商户消费",
            "示例餐厅",
            "午餐",
            "支出",
            "25.00",
            "零钱",
            "支付成功",
            "WX-1",
            "M-1",
            "测试",
        ]
    )
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return SimpleUploadedFile(
        "wechat.xlsx",
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@pytest.mark.django_db
def test_xlsx_and_single_file_zip_are_supported():
    xlsx_result = services.process_uploaded_bill(_xlsx_upload())
    assert xlsx_result.batch.status == ImportBatch.Status.WAITING_CONFIRMATION

    csv_bytes = (FIXTURES / "wechat/v1/normal.csv").read_bytes()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("folder/wechat.csv", csv_bytes)
    zip_result = services.process_uploaded_bill(
        SimpleUploadedFile("wechat.zip", output.getvalue(), content_type="application/zip")
    )
    assert zip_result.batch.status == ImportBatch.Status.WAITING_CONFIRMATION
    assert zip_result.batch.file_deleted_at is not None


@pytest.mark.django_db
def test_record_limit_failure_still_deletes_file(settings):
    settings.IMPORT_MAX_RECORDS = 1
    result = services.process_uploaded_bill(_fixture_upload("wechat/v1/normal.csv"))
    assert result.batch.status == ImportBatch.Status.FAILED
    assert result.batch.file_deleted_at is not None
    assert Transaction.objects.count() == 0


@pytest.mark.django_db
def test_cleanup_command_deletes_only_stale_tracked_file(settings, tmp_path):
    root = Path(settings.IMPORT_TMP_DIR)
    root.mkdir(parents=True)
    stale_file = root / "stale.csv"
    stale_file.write_text("stale", encoding="utf-8")
    batch = ImportBatch.objects.create(
        original_filename="stale.csv",
        file_sha256="a" * 64,
        temporary_file_path=str(stale_file),
    )
    ImportBatch.objects.filter(pk=batch.pk).update(uploaded_at=timezone.now() - timedelta(hours=25))
    call_command("cleanup_import_files")
    batch.refresh_from_db()
    assert not stale_file.exists()
    assert batch.file_deleted_at is not None
    assert batch.temporary_file_path == ""


@pytest.mark.django_db
def test_state_transitions_reject_invalid_changes():
    batch = ImportBatch.objects.create(original_filename="x.csv", file_sha256="b" * 64)
    services.transition_batch(batch=batch, target=ImportBatch.Status.PARSING)
    with pytest.raises(ValidationError, match="状态转换无效"):
        services.transition_batch(batch=batch, target=ImportBatch.Status.COMPLETED)


@pytest.mark.django_db
def test_upload_history_and_detail_pages_are_authenticated_and_read_only(authenticated_client):
    before = Transaction.objects.count()
    response = authenticated_client.post(
        reverse("imports:upload"),
        {"bill_file": _fixture_upload("alipay/v1/normal.csv")},
    )
    assert response.status_code == 302
    batch = ImportBatch.objects.get()
    assert response.url == reverse("imports:detail", args=[batch.id])
    assert authenticated_client.get(reverse("imports:index")).status_code == 200
    detail = authenticated_client.get(reverse("imports:detail", args=[batch.id]))
    assert detail.status_code == 200
    assert "仍与正式账本隔离" in detail.content.decode()
    assert Transaction.objects.count() == before
