import os
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.utils import timezone

from apps.core.backup import APP_VERSION, SCHEMA_VERSION
from apps.core.database_backup import (
    DatabaseBackupError,
    decrypt_database_backup,
    encrypted_file_sha256,
    load_master_key,
)
from apps.core.models import BackupRun


class Command(BaseCommand):
    help = "解密、验证并恢复 PostgreSQL custom-format 运维备份"

    def add_arguments(self, parser):
        parser.add_argument("backup_file", type=Path)
        parser.add_argument("--key-file", type=Path)
        parser.add_argument("--confirm-restore", action="store_true")

    def handle(self, *args, **options):
        if not options["confirm_restore"]:
            raise CommandError("必须显式提供 --confirm-restore。")
        source_path: Path = options["backup_file"]
        temporary_dir = Path(settings.DATABASE_BACKUP_TMP_DIR)
        temporary_dir.mkdir(parents=True, exist_ok=True)
        plain_path = None
        try:
            key = load_master_key(options["key_file"])
            with tempfile.NamedTemporaryFile(dir=temporary_dir, delete=False) as temporary:
                plain_path = Path(temporary.name)
            decrypt_database_backup(source_path, plain_path, key)
            self._run_pg_restore(plain_path, list_only=True)
            connections.close_all()
            self._run_pg_restore(plain_path, list_only=False)
            connections.close_all()
            BackupRun.objects.create(
                backup_type=BackupRun.BackupType.DATABASE_RESTORE,
                status=BackupRun.Status.SUCCEEDED,
                completed_at=timezone.now(),
                file_name=source_path.name[:255],
                file_size=source_path.stat().st_size,
                sha256=encrypted_file_sha256(source_path),
                app_version=APP_VERSION,
                schema_version=SCHEMA_VERSION,
            )
            self.stdout.write(self.style.SUCCESS("运维数据库恢复完成。"))
        except (DatabaseBackupError, OSError, subprocess.SubprocessError) as error:
            connections.close_all()
            self._record_failure(source_path, error)
            raise CommandError(self._safe_error(error)) from error
        finally:
            if plain_path:
                plain_path.unlink(missing_ok=True)

    @staticmethod
    def _safe_error(error: Exception) -> str:
        if isinstance(error, DatabaseBackupError):
            return str(error)[:500]
        if isinstance(error, subprocess.SubprocessError):
            return "PostgreSQL 恢复命令执行失败。"
        return "运维恢复文件操作失败。"

    def _record_failure(self, source_path: Path, error: Exception) -> None:
        try:
            BackupRun.objects.create(
                backup_type=BackupRun.BackupType.DATABASE_RESTORE,
                status=BackupRun.Status.FAILED,
                completed_at=timezone.now(),
                file_name=source_path.name[:255],
                app_version=APP_VERSION,
                schema_version=SCHEMA_VERSION,
                error_summary=self._safe_error(error),
            )
        except Exception:
            return

    @staticmethod
    def _run_pg_restore(source: Path, *, list_only: bool) -> None:
        if list_only:
            command = ["pg_restore", "--list", str(source)]
            environment = None
        else:
            database = settings.DATABASES["default"]
            environment = os.environ.copy()
            environment["PGPASSWORD"] = str(database["PASSWORD"])
            command = [
                "pg_restore",
                "--clean",
                "--if-exists",
                "--exit-on-error",
                "--single-transaction",
                "--no-owner",
                "--no-acl",
                "--no-password",
                "--host",
                str(database["HOST"]),
                "--port",
                str(database["PORT"]),
                "--username",
                str(database["USER"]),
                "--dbname",
                str(database["NAME"]),
                str(source),
            ]
        subprocess.run(
            command,
            env=environment,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
