import base64
import hashlib
import json
import os
import struct
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .backup import APP_VERSION, SCHEMA_VERSION

MAGIC = b"PFDBBACKUP1"
FORMAT_VERSION = 1
HEADER_LENGTH_BYTES = 4
MAX_HEADER_BYTES = 4096
NONCE_LENGTH = 12
TAG_LENGTH = 16
CHUNK_SIZE = 1024 * 1024


class DatabaseBackupError(Exception):
    pass


def load_master_key(path: str | Path | None = None) -> bytes:
    configured_path = path or os.environ.get("BACKUP_MASTER_KEY_FILE")
    if not configured_path:
        raise DatabaseBackupError("未配置运维备份主密钥文件。")
    key_path = Path(configured_path)
    try:
        encoded = key_path.read_text(encoding="ascii").strip()
        key = base64.b64decode(encoded, validate=True)
    except (OSError, UnicodeError, ValueError) as error:
        raise DatabaseBackupError("无法读取运维备份主密钥。") from error
    if len(key) != 32:
        raise DatabaseBackupError("运维备份主密钥必须是 Base64 编码的 32 字节随机值。")
    return key


def _canonical_json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _file_sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def encrypt_database_dump(source_path: Path, destination_path: Path, key: bytes) -> dict:
    if len(key) != 32:
        raise DatabaseBackupError("运维备份主密钥长度无效。")
    dump_sha256, dump_size = _file_sha256(source_path)
    if dump_size == 0:
        raise DatabaseBackupError("pg_dump 输出为空。")
    nonce = os.urandom(NONCE_LENGTH)
    header = {
        "app_version": APP_VERSION,
        "dump_sha256": dump_sha256,
        "dump_size": dump_size,
        "format_version": FORMAT_VERSION,
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "schema_version": SCHEMA_VERSION,
    }
    header_bytes = _canonical_json(header)
    authenticated_header = MAGIC + struct.pack(">I", len(header_bytes)) + header_bytes
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(authenticated_header)
    with source_path.open("rb") as source, destination_path.open("wb") as destination:
        destination.write(authenticated_header)
        while chunk := source.read(CHUNK_SIZE):
            destination.write(encryptor.update(chunk))
        destination.write(encryptor.finalize())
        destination.write(encryptor.tag)
        destination.flush()
        os.fsync(destination.fileno())
    return header


def _parse_header(source) -> tuple[dict, bytes, int]:
    magic = source.read(len(MAGIC))
    length_bytes = source.read(HEADER_LENGTH_BYTES)
    if magic != MAGIC or len(length_bytes) != HEADER_LENGTH_BYTES:
        raise DatabaseBackupError("不是有效的运维数据库备份。")
    header_length = struct.unpack(">I", length_bytes)[0]
    if not 1 <= header_length <= MAX_HEADER_BYTES:
        raise DatabaseBackupError("运维备份文件头无效。")
    header_bytes = source.read(header_length)
    if len(header_bytes) != header_length:
        raise DatabaseBackupError("运维备份文件不完整。")
    try:
        header = json.loads(header_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DatabaseBackupError("运维备份文件头无效。") from error
    if (
        not isinstance(header, dict)
        or header.get("format_version") != FORMAT_VERSION
        or header.get("app_version") != APP_VERSION
        or header.get("schema_version") != SCHEMA_VERSION
        or type(header.get("dump_size")) is not int
        or header["dump_size"] <= 0
    ):
        raise DatabaseBackupError("运维备份版本与当前系统不兼容。")
    try:
        nonce = base64.b64decode(header["nonce"], validate=True)
    except (KeyError, ValueError) as error:
        raise DatabaseBackupError("运维备份文件头无效。") from error
    if len(nonce) != NONCE_LENGTH or not isinstance(header.get("dump_sha256"), str):
        raise DatabaseBackupError("运维备份文件头无效。")
    authenticated_header = magic + length_bytes + header_bytes
    return header, authenticated_header, len(authenticated_header)


def decrypt_database_backup(source_path: Path, destination_path: Path, key: bytes) -> dict:
    if len(key) != 32:
        raise DatabaseBackupError("运维备份主密钥长度无效。")
    total_size = source_path.stat().st_size
    try:
        with source_path.open("rb") as source:
            header, authenticated_header, ciphertext_offset = _parse_header(source)
            ciphertext_size = total_size - ciphertext_offset - TAG_LENGTH
            if ciphertext_size <= 0:
                raise DatabaseBackupError("运维备份文件不完整。")
            source.seek(total_size - TAG_LENGTH)
            tag = source.read(TAG_LENGTH)
            source.seek(ciphertext_offset)
            decryptor = Cipher(
                algorithms.AES(key), modes.GCM(base64.b64decode(header["nonce"]), tag)
            ).decryptor()
            decryptor.authenticate_additional_data(authenticated_header)
            remaining = ciphertext_size
            digest = hashlib.sha256()
            size = 0
            with destination_path.open("wb") as destination:
                while remaining:
                    chunk = source.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        raise DatabaseBackupError("运维备份文件不完整。")
                    remaining -= len(chunk)
                    plaintext = decryptor.update(chunk)
                    digest.update(plaintext)
                    size += len(plaintext)
                    destination.write(plaintext)
                plaintext = decryptor.finalize()
                digest.update(plaintext)
                size += len(plaintext)
                destination.write(plaintext)
                destination.flush()
                os.fsync(destination.fileno())
    except InvalidTag as error:
        destination_path.unlink(missing_ok=True)
        raise DatabaseBackupError("运维备份认证失败，文件或密钥无效。") from error
    except Exception:
        destination_path.unlink(missing_ok=True)
        raise
    if size != header["dump_size"] or digest.hexdigest() != header["dump_sha256"]:
        destination_path.unlink(missing_ok=True)
        raise DatabaseBackupError("运维备份内容校验失败。")
    return header


def encrypted_file_sha256(path: Path) -> str:
    return _file_sha256(path)[0]
