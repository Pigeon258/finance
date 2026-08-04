import base64
import hashlib
import io
import json
import os
import struct
import zipfile
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.contrib.auth import SESSION_KEY
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.urls import Resolver404, resolve, reverse

from apps.accounts.models import Account, AccountReconciliation
from apps.budgets.models import (
    CategoryBudget,
    MonthlyBudget,
    PlannedCashFlow,
    PlannedCashFlowOccurrence,
    ReserveMovement,
)
from apps.core import backup
from apps.core.models import BackupRun, MaintenanceState, SystemPreference
from apps.credit.models import BillingCycle, BillingCycleItem, CreditCardProfile
from apps.imports.models import ImportAccountRule, ImportBatch, MerchantCategoryRule
from apps.installments.models import InstallmentAdjustment, InstallmentItem, InstallmentPlan
from apps.ledger import services as ledger_services
from apps.ledger.models import Category, Merchant, Tag, Transaction, TransactionTemplate

PASSWORD = "correct horse battery staple"
BACKUP_PASSPHRASE = "a separate backup passphrase"
TZ = ZoneInfo("Asia/Shanghai")


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_superuser(username="owner", password=PASSWORD)


@pytest.fixture
def business_data():
    bank = Account.objects.get(account_type=Account.AccountType.BANK)
    category = Category.objects.get(name="餐饮")
    ledger_transaction = ledger_services.create_expense(
        account=bank,
        category=category,
        amount=Decimal("12.34"),
        occurred_at=datetime(2026, 7, 13, 9, 30, tzinfo=TZ),
        channel=Transaction.Channel.BANK,
        counterparty="测试商家",
        note="原始备注",
    )
    category.is_active = False
    category.save(update_fields=["is_active", "updated_at"])
    MonthlyBudget.objects.create(
        month=datetime(2026, 7, 1).date(),
        total_expense_budget=Decimal("999.99"),
        savings_target=Decimal("100.00"),
        minimum_safety_buffer=Decimal("50.00"),
    )
    MerchantCategoryRule.objects.create(
        name="测试规则",
        match_target=MerchantCategoryRule.MatchTarget.MERCHANT,
        match_kind=MerchantCategoryRule.MatchKind.CONTAINS,
        pattern="测试",
        category=category,
    )
    preference = SystemPreference.objects.get()
    preference.large_expense_threshold = Decimal("321.09")
    preference.active_theme_id = "safe-default"
    preference.last_known_good_theme_id = "safe-default"
    preference.appearance_mode = SystemPreference.AppearanceMode.LIGHT
    preference.reduce_motion = True
    preference.show_theme_background = False
    preference.save(
        update_fields=[
            "active_theme_id",
            "appearance_mode",
            "large_expense_threshold",
            "last_known_good_theme_id",
            "reduce_motion",
            "show_theme_background",
            "updated_at",
        ]
    )
    return bank, category, ledger_transaction


def _custom_backup(records: list[dict], manifest: dict, passphrase: str) -> bytes:
    business_data = backup._canonical_json({"objects": records})
    manifest = dict(manifest)
    manifest["payload_sha256"] = hashlib.sha256(business_data).hexdigest()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(backup.MANIFEST_NAME, backup._canonical_json(manifest))
        bundle.writestr(backup.BUSINESS_DATA_NAME, business_data)
    salt = os.urandom(backup.SALT_LENGTH)
    nonce = os.urandom(backup.NONCE_LENGTH)
    header = {
        "app_version": backup.APP_VERSION,
        "format_version": backup.FORMAT_VERSION,
        "kdf": {
            "length": backup.KEY_LENGTH,
            "n": backup.KDF_N,
            "name": "scrypt",
            "p": backup.KDF_P,
            "r": backup.KDF_R,
        },
        "nonce": base64.b64encode(nonce).decode(),
        "salt": base64.b64encode(salt).decode(),
        "schema_version": backup.SCHEMA_VERSION,
    }
    header_bytes = backup._canonical_json(header)
    authenticated_header = backup.MAGIC + struct.pack(">I", len(header_bytes)) + header_bytes
    ciphertext = AESGCM(backup._derive_key(passphrase, salt)).encrypt(
        nonce, archive.getvalue(), authenticated_header
    )
    return authenticated_header + ciphertext


@pytest.mark.django_db
def test_encrypted_backup_round_trip_preserves_business_data_and_current_password(
    tmp_path, client, owner, business_data
):
    bank, category, ledger_transaction = business_data
    client.force_login(owner)
    assert Session.objects.filter(session_key=client.session.session_key).exists()
    file_bytes, filename = backup.create_user_backup(BACKUP_PASSPHRASE)

    assert filename.endswith(".pfbackup")
    assert file_bytes.startswith(backup.MAGIC)
    assert PASSWORD.encode() not in file_bytes
    assert owner.password.encode() not in file_bytes
    manifest, records = backup.decrypt_and_validate_backup(file_bytes, BACKUP_PASSPHRASE)
    labels = {row["model"] for row in records}
    assert "auth.user" not in labels
    assert "sessions.session" not in labels
    assert "imports.importbatch" not in labels
    assert manifest["model_counts"]["ledger.transaction"] == 1

    bank.name = "恢复前被修改"
    bank.save(update_fields=["name", "updated_at"])
    Transaction.objects.filter(pk=ledger_transaction.pk).update(note="恢复前被修改")
    Merchant.objects.create(name="恢复时应删除", normalized_name="恢复时应删除")
    ImportBatch.objects.create(original_filename="temporary.csv", file_sha256="a" * 64)

    with override_settings(BUSINESS_BACKUP_DIR=tmp_path):
        pre_restore_path = backup.restore_business_backup(
            file_bytes, BACKUP_PASSPHRASE, uploaded_filename="portable.pfbackup"
        )

    bank.refresh_from_db()
    category.refresh_from_db()
    ledger_transaction.refresh_from_db()
    owner.refresh_from_db()
    assert bank.name != "恢复前被修改"
    assert category.is_active is False
    assert ledger_transaction.amount == Decimal("12.34")
    assert ledger_transaction.note == "原始备注"
    assert MonthlyBudget.objects.get().total_expense_budget == Decimal("999.99")
    restored_preference = SystemPreference.objects.get()
    assert restored_preference.large_expense_threshold == Decimal("321.09")
    assert restored_preference.active_theme_id == "safe-default"
    assert restored_preference.last_known_good_theme_id == "safe-default"
    assert restored_preference.appearance_mode == SystemPreference.AppearanceMode.LIGHT
    assert restored_preference.reduce_motion is True
    assert restored_preference.show_theme_background is False
    assert MerchantCategoryRule.objects.get().pattern == "测试"
    assert not Merchant.objects.filter(normalized_name="恢复时应删除").exists()
    assert not ImportBatch.objects.exists()
    assert owner.check_password(PASSWORD)
    assert not Session.objects.exists()
    assert pre_restore_path.exists()
    assert pre_restore_path.read_bytes().startswith(backup.MAGIC)
    assert BackupRun.objects.filter(status=BackupRun.Status.SUCCEEDED).count() == 3
    run_metadata = json.dumps(list(BackupRun.objects.values()), default=str)
    assert BACKUP_PASSPHRASE not in run_metadata
    assert PASSWORD not in run_metadata


@pytest.mark.django_db
def test_every_business_model_type_round_trips_with_relationships(tmp_path):
    bank = Account.objects.get(account_type=Account.AccountType.BANK)
    card = Account.objects.get(account_type=Account.AccountType.CREDIT_CARD)
    category = Category.objects.get(name="餐饮")
    merchant = Merchant.objects.create(name="关联商家", normalized_name="关联商家")
    tag = Tag.objects.create(name="保留标签")
    ledger_transaction = ledger_services.create_expense(
        account=bank,
        category=category,
        amount=Decimal("66.60"),
        occurred_at=datetime(2026, 7, 13, 9, 30, tzinfo=TZ),
        channel=Transaction.Channel.BANK,
        merchant=merchant,
        tags=[tag],
    )
    card_transaction = ledger_services.create_credit_card_purchase(
        account=card,
        category=category,
        amount=Decimal("66.60"),
        occurred_at=datetime(2026, 7, 13, 9, 45, tzinfo=TZ),
        channel=Transaction.Channel.BANK,
    )
    TransactionTemplate.objects.create(
        name="保留模板",
        operation=TransactionTemplate.Operation.EXPENSE,
        amount=Decimal("10.01"),
        primary_account=bank,
        category=category,
        channel=Transaction.Channel.BANK,
    )
    AccountReconciliation.objects.create(
        account=bank,
        actual_balance=Decimal("100.00"),
        calculated_balance=Decimal("90.00"),
        difference=Decimal("10.00"),
        checked_at=datetime(2026, 7, 13, 10, 0, tzinfo=TZ),
        adjustment_transaction_id=ledger_transaction.pk,
    )
    profile = CreditCardProfile.objects.create(
        account=card,
        credit_limit=Decimal("5000.00"),
        personal_monthly_limit=Decimal("1000.00"),
        statement_day=15,
        due_day=5,
    )
    cycle = BillingCycle.objects.create(
        credit_card_profile=profile,
        cycle_start=datetime(2026, 7, 1).date(),
        cycle_end=datetime(2026, 7, 31).date(),
        due_date=datetime(2026, 8, 5).date(),
    )
    BillingCycleItem.objects.create(
        billing_cycle=cycle,
        transaction=card_transaction,
        item_type=BillingCycleItem.ItemType.CHARGE,
        allocated_amount=Decimal("66.60"),
    )
    plan = InstallmentPlan.objects.create(
        product_name="保留分期",
        purchase_date=datetime(2026, 7, 1).date(),
        original_price=Decimal("120.00"),
        category=category,
        source_type=InstallmentPlan.SourceType.CREDIT_CARD,
        installment_count=1,
        default_installment_amount=Decimal("120.00"),
        first_due_month=datetime(2026, 8, 1).date(),
        total_repayment_amount=Decimal("120.00"),
    )
    item = InstallmentItem.objects.create(
        plan=plan,
        sequence_number=1,
        due_date=datetime(2026, 8, 5).date(),
        due_month=datetime(2026, 8, 1).date(),
        planned_amount=Decimal("120.00"),
    )
    InstallmentAdjustment.objects.create(
        plan=plan,
        installment_item=item,
        adjustment_type=InstallmentAdjustment.AdjustmentType.AMOUNT_CHANGE,
        amount_delta=Decimal("-1.00"),
        effective_date=datetime(2026, 7, 13).date(),
    )
    monthly_budget = MonthlyBudget.objects.create(
        month=datetime(2026, 7, 1).date(),
        total_expense_budget=Decimal("1000.00"),
    )
    CategoryBudget.objects.create(
        monthly_budget=monthly_budget,
        category=category,
        budget_amount=Decimal("300.00"),
    )
    ReserveMovement.objects.create(
        movement_type=ReserveMovement.MovementType.CONTRIBUTION,
        amount=Decimal("50.00"),
        occurred_on=datetime(2026, 7, 13).date(),
        related_transaction=ledger_transaction,
    )
    cash_flow = PlannedCashFlow.objects.create(
        name="保留计划",
        direction=PlannedCashFlow.Direction.EXPENSE,
        amount=Decimal("20.00"),
        category=category,
        default_account=bank,
        reliability=PlannedCashFlow.Reliability.CERTAIN,
        recurrence_type=PlannedCashFlow.RecurrenceType.MONTHLY,
        start_date=datetime(2026, 7, 1).date(),
        day_of_month=20,
    )
    PlannedCashFlowOccurrence.objects.create(
        plan=cash_flow,
        due_date=datetime(2026, 7, 20).date(),
        planned_amount=Decimal("20.00"),
    )
    MerchantCategoryRule.objects.create(
        name="保留分类规则",
        match_target=MerchantCategoryRule.MatchTarget.MERCHANT,
        match_kind=MerchantCategoryRule.MatchKind.EXACT,
        pattern="关联商家",
        category=category,
    )
    ImportAccountRule.objects.create(
        name="保留账户规则",
        source=ImportBatch.Source.ALIPAY,
        match_kind=ImportAccountRule.MatchKind.EXACT,
        pattern="银行卡",
        account=bank,
    )
    original_counts = {
        model._meta.label_lower: model.objects.count() for model in backup.BACKUP_MODELS
    }
    assert all(count > 0 for count in original_counts.values())
    file_bytes, _ = backup.build_encrypted_backup(BACKUP_PASSPHRASE)

    Merchant.objects.create(name="额外数据", normalized_name="额外数据")
    with override_settings(BUSINESS_BACKUP_DIR=tmp_path):
        backup.restore_business_backup(file_bytes, BACKUP_PASSPHRASE)

    assert {
        model._meta.label_lower: model.objects.count() for model in backup.BACKUP_MODELS
    } == original_counts
    assert BillingCycleItem.objects.get().transaction_id == card_transaction.pk
    assert InstallmentItem.objects.get().plan.product_name == "保留分期"
    assert PlannedCashFlowOccurrence.objects.get().plan.name == "保留计划"
    assert Transaction.objects.get(pk=ledger_transaction.pk).tags.get().name == "保留标签"
    assert ImportAccountRule.objects.get().account_id == bank.pk


@pytest.mark.django_db
def test_each_backup_uses_new_salt_and_nonce(business_data):
    first, _ = backup.build_encrypted_backup(BACKUP_PASSPHRASE)
    second, _ = backup.build_encrypted_backup(BACKUP_PASSPHRASE)

    first_header, _, _ = backup._parse_header(first)
    second_header, _, _ = backup._parse_header(second)
    assert first_header["salt"] != second_header["salt"]
    assert first_header["nonce"] != second_header["nonce"]
    assert first != second


@pytest.mark.django_db
@pytest.mark.parametrize("mutation", ["wrong-password", "tamper"])
def test_wrong_passphrase_and_tampering_are_rejected(business_data, mutation):
    file_bytes, _ = backup.build_encrypted_backup(BACKUP_PASSPHRASE)
    if mutation == "tamper":
        changed = bytearray(file_bytes)
        changed[-1] ^= 1
        file_bytes = bytes(changed)
        passphrase = BACKUP_PASSPHRASE
    else:
        passphrase = "this is the wrong passphrase"

    with pytest.raises(backup.BackupError, match="口令错误|篡改"):
        backup.decrypt_and_validate_backup(file_bytes, passphrase)


@pytest.mark.django_db
def test_unknown_format_version_is_rejected(business_data):
    file_bytes, _ = backup.build_encrypted_backup(BACKUP_PASSPHRASE)
    offset = len(backup.MAGIC)
    header_length = struct.unpack(">I", file_bytes[offset : offset + 4])[0]
    header_end = offset + 4 + header_length
    header = json.loads(file_bytes[offset + 4 : header_end])
    header["format_version"] = 99
    changed_header = backup._canonical_json(header)
    changed = (
        backup.MAGIC
        + struct.pack(">I", len(changed_header))
        + changed_header
        + file_bytes[header_end:]
    )

    with pytest.raises(backup.BackupError, match="版本"):
        backup.decrypt_and_validate_backup(changed, BACKUP_PASSPHRASE)


@pytest.mark.django_db
def test_missing_model_field_is_rejected(business_data):
    _, manifest = backup._archive_bytes()
    records = backup._business_records()
    transaction = next(row for row in records if row["model"] == "ledger.transaction")
    del transaction["fields"]["amount"]
    malformed = _custom_backup(records, manifest, BACKUP_PASSPHRASE)

    with pytest.raises(backup.BackupError, match="缺少必要字段"):
        backup.decrypt_and_validate_backup(malformed, BACKUP_PASSPHRASE)


@override_settings(BUSINESS_BACKUP_MAX_UPLOAD_BYTES=10)
def test_oversized_backup_is_rejected_before_decryption():
    with pytest.raises(backup.BackupError, match="超过允许大小"):
        backup.decrypt_and_validate_backup(b"x" * 11, BACKUP_PASSPHRASE)


@pytest.mark.django_db
def test_oversized_decrypted_payload_is_rejected(business_data):
    file_bytes, _ = backup.build_encrypted_backup(BACKUP_PASSPHRASE)

    with (
        override_settings(BUSINESS_BACKUP_MAX_PLAINTEXT_BYTES=10),
        pytest.raises(backup.BackupError, match="解压后数据过大"),
    ):
        backup.decrypt_and_validate_backup(file_bytes, BACKUP_PASSPHRASE)


@pytest.mark.django_db
def test_restore_failure_rolls_back_all_business_tables(tmp_path, business_data):
    bank, _, ledger_transaction = business_data
    file_bytes, _ = backup.build_encrypted_backup(BACKUP_PASSPHRASE)
    original_name = bank.name
    original_note = ledger_transaction.note
    original_counts = {
        model._meta.label_lower: model.objects.count() for model in backup.BACKUP_MODELS
    }

    with (
        override_settings(BUSINESS_BACKUP_DIR=tmp_path),
        patch("apps.core.backup._restore_records", side_effect=RuntimeError("injected")),
        pytest.raises(RuntimeError, match="injected"),
    ):
        backup.restore_business_backup(file_bytes, BACKUP_PASSPHRASE)

    bank.refresh_from_db()
    ledger_transaction.refresh_from_db()
    assert bank.name == original_name
    assert ledger_transaction.note == original_note
    assert {
        model._meta.label_lower: model.objects.count() for model in backup.BACKUP_MODELS
    } == original_counts
    assert MaintenanceState.objects.get().enabled is False
    assert list(tmp_path.iterdir())
    assert all(path.suffix == ".pfbackup" for path in tmp_path.iterdir())
    assert BackupRun.objects.filter(status=BackupRun.Status.FAILED).exists()


@pytest.mark.django_db
def test_restore_rejects_financial_integrity_failure(tmp_path, business_data):
    file_bytes, _ = backup.build_encrypted_backup(BACKUP_PASSPHRASE)
    with (
        override_settings(BUSINESS_BACKUP_DIR=tmp_path),
        patch(
            "apps.core.backup.assert_financial_integrity",
            side_effect=ValidationError("invalid ledger"),
        ),
        pytest.raises(ValidationError),
    ):
        backup.restore_business_backup(file_bytes, BACKUP_PASSPHRASE)
    assert Transaction.objects.count() == 1


@pytest.mark.django_db
def test_concurrent_restore_is_rejected_without_disabling_existing_maintenance(
    tmp_path, business_data
):
    file_bytes, _ = backup.build_encrypted_backup(BACKUP_PASSPHRASE)
    MaintenanceState.objects.update_or_create(pk=1, defaults={"enabled": True})

    with (
        override_settings(BUSINESS_BACKUP_DIR=tmp_path),
        pytest.raises(backup.BackupError, match="另一个恢复任务"),
    ):
        backup.restore_business_backup(file_bytes, BACKUP_PASSPHRASE)

    assert MaintenanceState.objects.get(pk=1).enabled is True
    assert Transaction.objects.count() == 1


@pytest.mark.django_db
def test_backup_endpoints_require_login_and_no_plain_json_route_exists(client):
    for url_name in [
        "core:export-center",
        "core:transactions-csv",
        "core:monthly-statistics-csv",
    ]:
        response = client.get(reverse(url_name))
        assert response.status_code == 302
        assert response.url.startswith(reverse("core:login"))
    assert client.post(reverse("core:backup-download")).status_code == 302
    assert client.post(reverse("core:backup-restore")).status_code == 302
    with pytest.raises(Resolver404):
        resolve("/exports/backup.json")


@pytest.mark.django_db
def test_restore_requires_current_system_password(client, owner):
    client.force_login(owner)
    uploaded = io.BytesIO(b"not used")
    uploaded.name = "test.pfbackup"
    response = client.post(
        reverse("core:backup-restore"),
        {
            "system_password": "wrong system password",
            "backup_passphrase": BACKUP_PASSPHRASE,
            "backup_file": uploaded,
            "confirm_restore": True,
        },
    )
    assert response.status_code == 400
    assert "当前系统密码不正确" in response.content.decode()


@pytest.mark.django_db
def test_owner_can_download_and_restore_encrypted_backup_through_html_forms(
    tmp_path, client, owner
):
    client.force_login(owner)
    bank = Account.objects.get(account_type=Account.AccountType.BANK)
    original_name = bank.name
    download = client.post(
        reverse("core:backup-download"),
        {
            "backup_passphrase": BACKUP_PASSPHRASE,
            "backup_passphrase_confirm": BACKUP_PASSPHRASE,
        },
    )
    file_bytes = b"".join(download.streaming_content)
    assert download.status_code == 200
    assert file_bytes.startswith(backup.MAGIC)
    backup.decrypt_and_validate_backup(file_bytes, BACKUP_PASSPHRASE)

    bank.name = "待恢复名称"
    bank.save(update_fields=["name", "updated_at"])
    uploaded = SimpleUploadedFile(
        "portable.pfbackup", file_bytes, content_type="application/octet-stream"
    )
    with override_settings(BUSINESS_BACKUP_DIR=tmp_path):
        restored = client.post(
            reverse("core:backup-restore"),
            {
                "system_password": PASSWORD,
                "backup_passphrase": BACKUP_PASSPHRASE,
                "backup_file": uploaded,
                "confirm_restore": True,
            },
        )

    bank.refresh_from_db()
    assert restored.status_code == 302
    assert restored.url == f"{reverse('core:login')}?restored=1"
    assert bank.name == original_name
    assert SESSION_KEY not in client.session


@pytest.mark.django_db
def test_maintenance_mode_blocks_business_pages_but_not_health(client, owner):
    client.force_login(owner)
    MaintenanceState.objects.update_or_create(pk=1, defaults={"enabled": True})

    response = client.get(reverse("core:home"))

    assert response.status_code == 503
    assert "系统维护中" in response.content.decode()
    assert client.get(reverse("core:health-live")).status_code == 200
    assert client.get(reverse("core:health-ready")).status_code == 200
    assert client.get(reverse("core:login")).status_code == 302


@pytest.mark.django_db
def test_financial_integrity_command_detects_missing_entries(business_data):
    _, _, ledger_transaction = business_data
    call_command("check_financial_integrity", verbosity=0)
    ledger_transaction.entries.all().delete()

    with pytest.raises(CommandError, match="账本条目"):
        call_command("check_financial_integrity", verbosity=0)
