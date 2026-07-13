import os
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.core.backup import APP_VERSION, SCHEMA_VERSION
from apps.core.database_backup import (
    DatabaseBackupError,
    decrypt_database_backup,
    encrypt_database_dump,
    encrypted_file_sha256,
    load_master_key,
)
from apps.core.models import BackupRun

BACKUP_TYPES = {
    "daily": BackupRun.BackupType.DATABASE_DAILY,
    "weekly": BackupRun.BackupType.DATABASE_WEEKLY,
    "deployment": BackupRun.BackupType.DATABASE_DEPLOYMENT,
    "manual": BackupRun.BackupType.DATABASE_MANUAL,
}


def rotate_backups(directory: Path, *, kind: str, keep: int) -> None:
    files = sorted(
        directory.glob(f"db-{kind}-*.dump.enc"),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
    )
    for expired in files[:-keep] if keep > 0 else files:
        expired.unlink()


class Command(BaseCommand):
    help = "创建、加密并验证 PostgreSQL custom-format 运维备份"

    def add_arguments(self, parser):
        parser.add_argument("--kind", choices=BACKUP_TYPES, default="manual")
        parser.add_argument("--output-dir", type=Path, default=Path(settings.DATABASE_BACKUP_DIR))
        parser.add_argument("--key-file", type=Path)
        parser.add_argument("--skip-rotation", action="store_true")

    def handle(self, *args, **options):
        kind = options["kind"]
        output_dir: Path = options["output_dir"]
        run = BackupRun.objects.create(
            backup_type=BACKUP_TYPES[kind],
            app_version=APP_VERSION,
            schema_version=SCHEMA_VERSION,
        )
        plain_path = None
        verify_path = None
        temporary_encrypted_path = None
        final_path = None
        try:
            key = load_master_key(options["key_file"])
            output_dir.mkdir(parents=True, exist_ok=True)
            temporary_dir = Path(settings.DATABASE_BACKUP_TMP_DIR)
            temporary_dir.mkdir(parents=True, exist_ok=True)
            timestamp = timezone.now().strftime("%Y%m%dT%H%M%SZ")
            filename = f"db-{kind}-{timestamp}-{run.pk}.dump.enc"
            final_path = output_dir / filename
            with tempfile.NamedTemporaryFile(dir=temporary_dir, delete=False) as temporary:
                plain_path = Path(temporary.name)
            with tempfile.NamedTemporaryFile(dir=output_dir, delete=False) as temporary:
                temporary_encrypted_path = Path(temporary.name)
            self._run_pg_dump(plain_path)
            encrypt_database_dump(plain_path, temporary_encrypted_path, key)
            with tempfile.NamedTemporaryFile(dir=temporary_dir, delete=False) as temporary:
                verify_path = Path(temporary.name)
            decrypt_database_backup(temporary_encrypted_path, verify_path, key)
            self._run_pg_restore_list(verify_path)
            os.replace(temporary_encrypted_path, final_path)
            temporary_encrypted_path = None
            if not options["skip_rotation"]:
                if kind == "daily":
                    rotate_backups(output_dir, kind=kind, keep=settings.DATABASE_BACKUP_DAILY_KEEP)
                elif kind == "weekly":
                    rotate_backups(output_dir, kind=kind, keep=settings.DATABASE_BACKUP_WEEKLY_KEEP)
            run.status = BackupRun.Status.SUCCEEDED
            run.completed_at = timezone.now()
            run.file_name = filename
            run.file_size = final_path.stat().st_size
            run.sha256 = encrypted_file_sha256(final_path)
            run.save()
            self.stdout.write(self.style.SUCCESS(f"运维数据库备份已验证：{filename}"))
        except (DatabaseBackupError, OSError, subprocess.SubprocessError) as error:
            run.status = BackupRun.Status.FAILED
            run.completed_at = timezone.now()
            run.error_summary = self._safe_error(error)
            run.save()
            if final_path:
                final_path.unlink(missing_ok=True)
            raise CommandError(run.error_summary) from error
        finally:
            for path in (plain_path, verify_path, temporary_encrypted_path):
                if path:
                    path.unlink(missing_ok=True)

    @staticmethod
    def _safe_error(error: Exception) -> str:
        if isinstance(error, DatabaseBackupError):
            return str(error)[:500]
        if isinstance(error, subprocess.SubprocessError):
            return "PostgreSQL 备份或验证命令执行失败。"
        return "运维备份文件操作失败。"

    @staticmethod
    def _run_pg_dump(destination: Path) -> None:
        database = settings.DATABASES["default"]
        environment = os.environ.copy()
        environment["PGPASSWORD"] = str(database["PASSWORD"])
        subprocess.run(
            [
                "pg_dump",
                "--format=custom",
                "--no-owner",
                "--no-acl",
                "--no-password",
                "--host",
                str(database["HOST"]),
                "--port",
                str(database["PORT"]),
                "--username",
                str(database["USER"]),
                "--file",
                str(destination),
                str(database["NAME"]),
            ],
            env=environment,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @staticmethod
    def _run_pg_restore_list(source: Path) -> None:
        subprocess.run(
            ["pg_restore", "--list", str(source)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
