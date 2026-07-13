import base64
import os
import subprocess
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.accounts.models import Account, AccountReconciliation
from apps.core.database_backup import (
    DatabaseBackupError,
    decrypt_database_backup,
    encrypt_database_dump,
    load_master_key,
)
from apps.core.integrity import financial_integrity_issues
from apps.core.models import BackupRun, MaintenanceState
from apps.ledger import services as ledger_services
from apps.ledger.models import Category, Transaction

TZ = ZoneInfo("Asia/Shanghai")


def _key_file(tmp_path: Path, key: bytes) -> Path:
    path = tmp_path / "backup-master-key"
    path.write_text(base64.b64encode(key).decode(), encoding="ascii")
    return path


def test_database_backup_crypto_round_trip_and_tamper_rejection(tmp_path):
    key = os.urandom(32)
    source = tmp_path / "source.dump"
    encrypted = tmp_path / "database.dump.enc"
    restored = tmp_path / "restored.dump"
    source.write_bytes(b"PGDMP" + os.urandom(2 * 1024 * 1024 + 17))

    header = encrypt_database_dump(source, encrypted, key)
    restored_header = decrypt_database_backup(encrypted, restored, key)

    assert restored.read_bytes() == source.read_bytes()
    assert restored_header == header
    assert header["dump_size"] == source.stat().st_size

    changed = bytearray(encrypted.read_bytes())
    changed[-1] ^= 1
    encrypted.write_bytes(changed)
    with pytest.raises(DatabaseBackupError, match="认证失败"):
        decrypt_database_backup(encrypted, restored, key)
    assert not restored.exists()


def test_database_backup_master_key_requires_base64_32_bytes(tmp_path):
    key = os.urandom(32)
    assert load_master_key(_key_file(tmp_path, key)) == key
    invalid = tmp_path / "invalid-key"
    invalid.write_text("not a key", encoding="ascii")
    with pytest.raises(DatabaseBackupError, match="主密钥"):
        load_master_key(invalid)


@pytest.mark.django_db
def test_database_backup_command_dumps_encrypts_verifies_and_rotates(tmp_path):
    output_dir = tmp_path / "backups"
    runtime_dir = tmp_path / "runtime"
    key = os.urandom(32)
    key_file = _key_file(tmp_path, key)

    def fake_run(command, **kwargs):
        if command[0] == "pg_dump":
            destination = Path(command[command.index("--file") + 1])
            destination.write_bytes(b"PGDMP-test-custom-format")
        assert kwargs["stderr"] is subprocess.DEVNULL
        return subprocess.CompletedProcess(command, 0)

    with (
        override_settings(
            DATABASE_BACKUP_TMP_DIR=runtime_dir,
            DATABASE_BACKUP_DAILY_KEEP=1,
        ),
        patch("apps.core.management.commands.database_backup.subprocess.run", side_effect=fake_run),
    ):
        call_command(
            "database_backup",
            kind="daily",
            output_dir=output_dir,
            key_file=key_file,
        )
        call_command(
            "database_backup",
            kind="daily",
            output_dir=output_dir,
            key_file=key_file,
        )

    encrypted_files = list(output_dir.glob("*.dump.enc"))
    assert len(encrypted_files) == 1
    decrypted = tmp_path / "verified.dump"
    decrypt_database_backup(encrypted_files[0], decrypted, key)
    assert decrypted.read_bytes() == b"PGDMP-test-custom-format"
    assert not list(runtime_dir.iterdir())
    assert (
        BackupRun.objects.filter(
            backup_type=BackupRun.BackupType.DATABASE_DAILY,
            status=BackupRun.Status.SUCCEEDED,
        ).count()
        == 2
    )


@pytest.mark.django_db
def test_database_backup_command_fails_safely_without_sensitive_error(tmp_path):
    key_file = _key_file(tmp_path, os.urandom(32))
    with (
        override_settings(DATABASE_BACKUP_TMP_DIR=tmp_path / "runtime"),
        patch(
            "apps.core.management.commands.database_backup.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["pg_dump"]),
        ),
        pytest.raises(CommandError, match="命令执行失败"),
    ):
        call_command(
            "database_backup",
            kind="manual",
            output_dir=tmp_path / "backups",
            key_file=key_file,
        )

    run = BackupRun.objects.get(backup_type=BackupRun.BackupType.DATABASE_MANUAL)
    assert run.status == BackupRun.Status.FAILED
    assert "password" not in run.error_summary.lower()
    assert not list((tmp_path / "backups").glob("*.enc"))


@pytest.mark.django_db
def test_database_restore_command_uses_single_transaction_and_cleans_plaintext(tmp_path):
    key = os.urandom(32)
    key_file = _key_file(tmp_path, key)
    dump = tmp_path / "source.dump"
    dump.write_bytes(b"PGDMP-restorable")
    encrypted = tmp_path / "db-manual-test.dump.enc"
    encrypt_database_dump(dump, encrypted, key)
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    runtime = tmp_path / "runtime"
    with (
        override_settings(DATABASE_BACKUP_TMP_DIR=runtime),
        patch(
            "apps.core.management.commands.database_restore.subprocess.run", side_effect=fake_run
        ),
    ):
        call_command(
            "database_restore",
            encrypted,
            key_file=key_file,
            confirm_restore=True,
        )

    restore_command = next(command for command in commands if "--clean" in command)
    assert "--single-transaction" in restore_command
    assert "--exit-on-error" in restore_command
    assert not list(runtime.iterdir())
    assert BackupRun.objects.filter(
        backup_type=BackupRun.BackupType.DATABASE_RESTORE,
        status=BackupRun.Status.SUCCEEDED,
    ).exists()


@pytest.mark.django_db
def test_maintenance_mode_command_is_recoverable():
    call_command("maintenance_mode", "enable")
    assert MaintenanceState.objects.get(pk=1).enabled is True
    call_command("maintenance_mode", "disable")
    assert MaintenanceState.objects.get(pk=1).enabled is False


@pytest.mark.django_db
def test_integrity_check_reports_sign_refund_and_reconciliation_categories():
    bank = Account.objects.get(account_type=Account.AccountType.BANK)
    category = Category.objects.get(name="餐饮")
    expense = ledger_services.create_expense(
        account=bank,
        category=category,
        amount=Decimal("10.00"),
        occurred_at=datetime(2026, 7, 13, 12, 0, tzinfo=TZ),
        channel=Transaction.Channel.BANK,
    )
    expense.entries.update(balance_delta=Decimal("10.00"))
    refund = Transaction.objects.create(
        transaction_type=Transaction.TransactionType.REFUND,
        amount=Decimal("11.00"),
        occurred_at=datetime(2026, 7, 14, 12, 0, tzinfo=TZ),
        budget_month=datetime(2026, 7, 1).date(),
        category=category,
        channel=Transaction.Channel.BANK,
        related_transaction=expense,
    )
    refund.entries.create(account=bank, balance_delta=Decimal("11.00"))
    AccountReconciliation.objects.create(
        account=bank,
        actual_balance=Decimal("0.00"),
        calculated_balance=Decimal("0.00"),
        difference=Decimal("0.00"),
        checked_at=datetime(2026, 7, 14, 12, 0, tzinfo=TZ),
        adjustment_transaction_id=999999,
    )

    codes = {issue.code for issue in financial_integrity_issues()}

    assert {"EXPENSE_SIGN", "REFUND_LIMIT", "RECONCILIATION_LINK"} <= codes
