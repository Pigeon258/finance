import base64
import hashlib
import io
import json
import os
import struct
import tempfile
import zipfile
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from django.conf import settings
from django.contrib.sessions.models import Session
from django.core import serializers
from django.core.exceptions import ValidationError
from django.core.management.color import no_style
from django.db import connection, transaction
from django.utils import timezone

from apps.accounts import backup as accounts_backup
from apps.budgets import backup as budgets_backup
from apps.credit import backup as credit_backup
from apps.imports import backup as imports_backup
from apps.imports.models import (
    ImportBatch,
    ImportDuplicateCandidate,
    ImportRecord,
)
from apps.installments import backup as installments_backup
from apps.ledger import backup as ledger_backup

from .integrity import assert_financial_integrity
from .models import BackupRun, MaintenanceState, SystemPreference

MAGIC = b"PFBACKUP1"
FORMAT_VERSION = 1
SCHEMA_VERSION = 1
HEADER_LENGTH_BYTES = 4
MAX_HEADER_BYTES = 4096
KDF_N = 2**14
KDF_R = 8
KDF_P = 1
KEY_LENGTH = 32
SALT_LENGTH = 16
NONCE_LENGTH = 12
MANIFEST_NAME = "manifest.json"
BUSINESS_DATA_NAME = "business-data.json"

MODULE_SCHEMA_VERSIONS = {
    accounts_backup.BACKUP_SCHEMA_VERSION,
    ledger_backup.BACKUP_SCHEMA_VERSION,
    credit_backup.BACKUP_SCHEMA_VERSION,
    installments_backup.BACKUP_SCHEMA_VERSION,
    budgets_backup.BACKUP_SCHEMA_VERSION,
    imports_backup.BACKUP_SCHEMA_VERSION,
}
if MODULE_SCHEMA_VERSIONS != {SCHEMA_VERSION}:
    raise RuntimeError("业务模块备份 schema 版本不一致。")

try:
    APP_VERSION = version("personal-finance")
except PackageNotFoundError:  # pragma: no cover - editable installs provide package metadata.
    APP_VERSION = "0.1.0"

BACKUP_MODELS = (
    SystemPreference,
    *accounts_backup.BACKUP_MODELS,
    *ledger_backup.BACKUP_MODELS,
    *credit_backup.BACKUP_MODELS,
    *installments_backup.BACKUP_MODELS,
    *budgets_backup.BACKUP_MODELS,
    *imports_backup.BACKUP_MODELS,
)

PURGE_MODELS = (
    ImportDuplicateCandidate,
    ImportRecord,
    ImportBatch,
    *reversed(BACKUP_MODELS),
)


class BackupError(Exception):
    pass


def _model_label(model) -> str:
    return model._meta.label_lower


def _serialized_field_names(model) -> tuple[str, ...]:
    return tuple(
        field.name
        for field in model._meta.local_fields
        if field.serialize and not field.primary_key
    )


def _canonical_json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _business_records() -> list[dict]:
    records: list[dict] = []
    for model in BACKUP_MODELS:
        serialized = serializers.serialize(
            "json",
            model.objects.order_by("pk"),
            fields=_serialized_field_names(model),
            use_natural_foreign_keys=False,
            use_natural_primary_keys=False,
        )
        records.extend(json.loads(serialized))
    return records


def _archive_bytes() -> tuple[bytes, dict]:
    records = _business_records()
    business_data = _canonical_json({"objects": records})
    counts = {
        _model_label(model): sum(1 for row in records if row["model"] == _model_label(model))
        for model in BACKUP_MODELS
    }
    manifest = {
        "app_version": APP_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "model_counts": counts,
        "payload_sha256": hashlib.sha256(business_data).hexdigest(),
        "schema_version": SCHEMA_VERSION,
    }
    manifest_data = _canonical_json(manifest)
    if len(manifest_data) + len(business_data) > settings.BUSINESS_BACKUP_MAX_PLAINTEXT_BYTES:
        raise BackupError("业务数据过大，无法生成备份。")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(MANIFEST_NAME, manifest_data)
        bundle.writestr(BUSINESS_DATA_NAME, business_data)
    plaintext = archive.getvalue()
    if len(plaintext) > settings.BUSINESS_BACKUP_MAX_PLAINTEXT_BYTES:
        raise BackupError("业务数据过大，无法生成备份。")
    return plaintext, manifest


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=KEY_LENGTH, n=KDF_N, r=KDF_R, p=KDF_P).derive(
        passphrase.encode("utf-8")
    )


def build_encrypted_backup(passphrase: str) -> tuple[bytes, dict]:
    if len(passphrase) < 12:
        raise BackupError("备份口令至少需要 12 个字符。")
    with transaction.atomic():
        plaintext_bytes, manifest = _archive_bytes()
    plaintext = bytearray(plaintext_bytes)
    del plaintext_bytes
    salt = os.urandom(SALT_LENGTH)
    nonce = os.urandom(NONCE_LENGTH)
    header = {
        "app_version": APP_VERSION,
        "format_version": FORMAT_VERSION,
        "kdf": {"length": KEY_LENGTH, "n": KDF_N, "name": "scrypt", "p": KDF_P, "r": KDF_R},
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "salt": base64.b64encode(salt).decode("ascii"),
        "schema_version": SCHEMA_VERSION,
    }
    header_bytes = _canonical_json(header)
    authenticated_header = MAGIC + struct.pack(">I", len(header_bytes)) + header_bytes
    try:
        ciphertext = AESGCM(_derive_key(passphrase, salt)).encrypt(
            nonce, plaintext, authenticated_header
        )
    finally:
        plaintext[:] = b"\0" * len(plaintext)
    return authenticated_header + ciphertext, manifest


def _parse_header(file_bytes: bytes) -> tuple[dict, bytes, bytes]:
    minimum_size = len(MAGIC) + HEADER_LENGTH_BYTES + 16
    if len(file_bytes) < minimum_size or not file_bytes.startswith(MAGIC):
        raise BackupError("不是有效的 .pfbackup 文件。")
    offset = len(MAGIC)
    header_length = struct.unpack(">I", file_bytes[offset : offset + HEADER_LENGTH_BYTES])[0]
    if not 1 <= header_length <= MAX_HEADER_BYTES:
        raise BackupError("备份文件头无效。")
    header_end = offset + HEADER_LENGTH_BYTES + header_length
    if header_end + 16 > len(file_bytes):
        raise BackupError("备份文件不完整。")
    authenticated_header = file_bytes[:header_end]
    try:
        header = json.loads(file_bytes[offset + HEADER_LENGTH_BYTES : header_end].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BackupError("备份文件头无效。") from error
    expected_kdf = {"length": KEY_LENGTH, "n": KDF_N, "name": "scrypt", "p": KDF_P, "r": KDF_R}
    if not isinstance(header, dict) or (
        header.get("format_version") != FORMAT_VERSION
        or header.get("schema_version") != SCHEMA_VERSION
        or header.get("app_version") != APP_VERSION
        or header.get("kdf") != expected_kdf
    ):
        raise BackupError("备份版本与当前系统不兼容。")
    try:
        salt = base64.b64decode(header["salt"], validate=True)
        nonce = base64.b64decode(header["nonce"], validate=True)
    except (KeyError, ValueError) as error:
        raise BackupError("备份文件头无效。") from error
    if len(salt) != SALT_LENGTH or len(nonce) != NONCE_LENGTH:
        raise BackupError("备份文件头无效。")
    return header, authenticated_header, file_bytes[header_end:]


def _read_archive(plaintext: bytes) -> tuple[dict, list[dict]]:
    try:
        with zipfile.ZipFile(io.BytesIO(plaintext)) as bundle:
            names = bundle.namelist()
            if len(names) != 2 or set(names) != {MANIFEST_NAME, BUSINESS_DATA_NAME}:
                raise BackupError("备份内部结构无效。")
            total_size = sum(item.file_size for item in bundle.infolist())
            if total_size > settings.BUSINESS_BACKUP_MAX_PLAINTEXT_BYTES:
                raise BackupError("备份解压后数据过大。")
            manifest_bytes = bundle.read(MANIFEST_NAME)
            business_data = bundle.read(BUSINESS_DATA_NAME)
    except (zipfile.BadZipFile, RuntimeError) as error:
        raise BackupError("备份内部结构无效。") from error
    try:
        manifest = json.loads(manifest_bytes)
        payload = json.loads(business_data)
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError) as error:
        raise BackupError("备份 JSON 数据无效。") from error
    if not isinstance(manifest, dict) or hashlib.sha256(business_data).hexdigest() != manifest.get(
        "payload_sha256"
    ):
        raise BackupError("备份数据校验失败。")
    if not isinstance(payload, dict) or set(payload) != {"objects"}:
        raise BackupError("备份业务数据结构无效。")
    return manifest, payload["objects"]


def decrypt_and_validate_backup(file_bytes: bytes, passphrase: str) -> tuple[dict, list[dict]]:
    if len(file_bytes) > settings.BUSINESS_BACKUP_MAX_UPLOAD_BYTES:
        raise BackupError("备份文件超过允许大小。")
    header, authenticated_header, ciphertext = _parse_header(file_bytes)
    salt = base64.b64decode(header["salt"])
    nonce = base64.b64decode(header["nonce"])
    try:
        plaintext_bytes = AESGCM(_derive_key(passphrase, salt)).decrypt(
            nonce, ciphertext, authenticated_header
        )
    except InvalidTag as error:
        raise BackupError("备份口令错误或文件已被篡改。") from error
    plaintext = bytearray(plaintext_bytes)
    del plaintext_bytes
    try:
        manifest, records = _read_archive(plaintext)
        _validate_payload(manifest, records)
        return manifest, records
    finally:
        plaintext[:] = b"\0" * len(plaintext)


def _validate_payload(manifest: dict, records: list[dict]) -> None:
    expected_labels = [_model_label(model) for model in BACKUP_MODELS]
    if not isinstance(manifest, dict) or set(manifest) != {
        "app_version",
        "created_at",
        "model_counts",
        "payload_sha256",
        "schema_version",
    }:
        raise BackupError("备份 manifest 缺少必要字段。")
    if manifest["schema_version"] != SCHEMA_VERSION or manifest["app_version"] != APP_VERSION:
        raise BackupError("备份版本与当前系统不兼容。")
    if not isinstance(records, list):
        raise BackupError("备份对象列表无效。")
    model_map = {_model_label(model): model for model in BACKUP_MODELS}
    counts = {label: 0 for label in expected_labels}
    seen_primary_keys: set[tuple[str, int]] = set()
    for row in records:
        if not isinstance(row, dict) or set(row) != {"model", "pk", "fields"}:
            raise BackupError("备份对象结构无效。")
        model = model_map.get(row["model"])
        if model is None or type(row["pk"]) is not int or row["pk"] <= 0:
            raise BackupError("备份包含未知模型或无效主键。")
        identity = (row["model"], row["pk"])
        if identity in seen_primary_keys:
            raise BackupError("备份包含重复对象主键。")
        seen_primary_keys.add(identity)
        if not isinstance(row["fields"], dict) or set(row["fields"]) != set(
            _serialized_field_names(model)
        ):
            raise BackupError(f"备份模型 {row['model']} 缺少必要字段。")
        counts[row["model"]] += 1
    if manifest["model_counts"] != counts:
        raise BackupError("备份对象数量校验失败。")
    if counts[_model_label(SystemPreference)] != 1:
        raise BackupError("备份缺少唯一的系统设置。")
    try:
        list(serializers.deserialize("json", json.dumps(records), ignorenonexistent=False))
    except Exception as error:
        raise BackupError("备份字段值无效。") from error


def _delete_restore_scope() -> None:
    with connection.cursor() as cursor:
        for model in PURGE_MODELS:
            cursor.execute(f"DELETE FROM {connection.ops.quote_name(model._meta.db_table)}")


def _restore_records(records: list[dict]) -> None:
    by_model: dict[str, list[dict]] = {_model_label(model): [] for model in BACKUP_MODELS}
    for row in records:
        by_model[row["model"]].append(row)
    for model in BACKUP_MODELS:
        rows = sorted(by_model[_model_label(model)], key=lambda row: row["pk"])
        if not rows:
            continue
        for deserialized in serializers.deserialize("json", json.dumps(rows)):
            deserialized.save(using=connection.alias)
    connection.check_constraints()
    try:
        for model in BACKUP_MODELS:
            for restored_object in model.objects.all().iterator():
                restored_object.full_clean()
    except ValidationError as error:
        raise BackupError("恢复数据未通过模型完整性校验。") from error
    sequence_sql = connection.ops.sequence_reset_sql(no_style(), list(BACKUP_MODELS))
    with connection.cursor() as cursor:
        for statement in sequence_sql:
            cursor.execute(statement)


def _complete_run(run: BackupRun, *, file_name: str = "", file_bytes: bytes | None = None) -> None:
    run.status = BackupRun.Status.SUCCEEDED
    run.completed_at = timezone.now()
    run.file_name = file_name
    if file_bytes is not None:
        run.file_size = len(file_bytes)
        run.sha256 = hashlib.sha256(file_bytes).hexdigest()
    run.save()


def _fail_run(run: BackupRun, error: Exception) -> None:
    run.status = BackupRun.Status.FAILED
    run.completed_at = timezone.now()
    run.error_summary = str(error)[:500]
    run.save()


@transaction.atomic
def _enter_maintenance() -> None:
    state = MaintenanceState.objects.select_for_update().get(pk=MaintenanceState.SINGLETON_ID)
    if state.enabled:
        raise BackupError("另一个恢复任务正在执行，请稍后重试。")
    state.enabled = True
    state.save(update_fields=["enabled", "updated_at"])


def _leave_maintenance() -> None:
    MaintenanceState.objects.filter(pk=MaintenanceState.SINGLETON_ID).update(
        enabled=False, updated_at=timezone.now()
    )


def create_user_backup(passphrase: str) -> tuple[bytes, str]:
    run = BackupRun.objects.create(
        backup_type=BackupRun.BackupType.USER_EXPORT,
        app_version=APP_VERSION,
        schema_version=SCHEMA_VERSION,
    )
    try:
        file_bytes, _ = build_encrypted_backup(passphrase)
        decrypt_and_validate_backup(file_bytes, passphrase)
        filename = f"personal-finance-{timezone.localdate():%Y%m%d}.pfbackup"
        _complete_run(run, file_name=filename, file_bytes=file_bytes)
        return file_bytes, filename
    except Exception as error:
        _fail_run(run, error)
        raise


def _persist_pre_restore_backup(passphrase: str) -> Path:
    run = BackupRun.objects.create(
        backup_type=BackupRun.BackupType.PRE_RESTORE,
        app_version=APP_VERSION,
        schema_version=SCHEMA_VERSION,
    )
    temporary_path = None
    try:
        file_bytes, _ = build_encrypted_backup(passphrase)
        decrypt_and_validate_backup(file_bytes, passphrase)
        directory = Path(settings.BUSINESS_BACKUP_DIR)
        directory.mkdir(parents=True, exist_ok=True)
        filename = f"pre-restore-{timezone.now():%Y%m%dT%H%M%S%fZ}-{run.pk}.pfbackup"
        destination = directory / filename
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as temporary:
            temporary.write(file_bytes)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, destination)
        _complete_run(run, file_name=filename, file_bytes=file_bytes)
        return destination
    except Exception as error:
        _fail_run(run, error)
        raise
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def restore_business_backup(
    file_bytes: bytes, passphrase: str, *, uploaded_filename: str = ""
) -> Path:
    run = BackupRun.objects.create(
        backup_type=BackupRun.BackupType.RESTORE,
        app_version=APP_VERSION,
        schema_version=SCHEMA_VERSION,
    )
    try:
        _, records = decrypt_and_validate_backup(file_bytes, passphrase)
        pre_restore_path = _persist_pre_restore_backup(passphrase)
        _enter_maintenance()
        try:
            with transaction.atomic():
                _delete_restore_scope()
                _restore_records(records)
                assert_financial_integrity()
                Session.objects.all().delete()
        finally:
            _leave_maintenance()
        _complete_run(
            run,
            file_name=Path(uploaded_filename).name[:255],
            file_bytes=file_bytes,
        )
        return pre_restore_path
    except Exception as error:
        _fail_run(run, error)
        raise
