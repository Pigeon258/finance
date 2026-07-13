import hashlib
import os
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from django.conf import settings


class UnsafeImportFile(Exception):
    pass


@dataclass(frozen=True)
class FileLimits:
    max_upload_bytes: int
    max_extracted_bytes: int
    max_zip_files: int

    @classmethod
    def from_settings(cls):
        return cls(
            settings.IMPORT_MAX_UPLOAD_BYTES,
            settings.IMPORT_MAX_EXTRACTED_BYTES,
            settings.IMPORT_MAX_ZIP_FILES,
        )


@dataclass(frozen=True)
class StoredUpload:
    path: Path
    original_filename: str
    file_sha256: str


@dataclass(frozen=True)
class PreparedBillFile:
    path: Path
    cleanup_paths: tuple[Path, ...] = ()


ALLOWED_CONTENT_TYPES = {
    ".csv": {
        "text/csv",
        "text/plain",
        "application/csv",
        "application/vnd.ms-excel",
        "application/octet-stream",
    },
    ".xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/zip",
        "application/octet-stream",
    },
    ".zip": {"application/zip", "application/x-zip-compressed", "application/octet-stream"},
}


def import_temp_root() -> Path:
    root = Path(settings.IMPORT_TMP_DIR).expanduser().resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def _safe_original_filename(name: str) -> str:
    name = str(name or "")
    if not name or "/" in name or "\\" in name or Path(name).name != name:
        raise UnsafeImportFile("上传文件名不安全。")
    sanitized = "".join(character for character in name if character.isprintable()).strip()
    if not sanitized or len(sanitized) > 255:
        raise UnsafeImportFile("上传文件名无效。")
    if Path(sanitized).suffix.lower() not in ALLOWED_CONTENT_TYPES:
        raise UnsafeImportFile("仅支持 CSV、XLSX 或 ZIP 文件。")
    return sanitized


def store_uploaded_file(uploaded_file, *, limits: FileLimits | None = None) -> StoredUpload:
    limits = limits or FileLimits.from_settings()
    original_filename = _safe_original_filename(uploaded_file.name)
    suffix = Path(original_filename).suffix.lower()
    declared_type = (getattr(uploaded_file, "content_type", "") or "").lower()
    if declared_type and declared_type not in ALLOWED_CONTENT_TYPES[suffix]:
        raise UnsafeImportFile("文件类型声明与扩展名不匹配。")
    root = import_temp_root()
    descriptor, temporary_name = tempfile.mkstemp(prefix="upload-", suffix=suffix, dir=root)
    path = Path(temporary_name)
    digest = hashlib.sha256()
    written = 0
    try:
        with os.fdopen(descriptor, "wb") as output:
            for chunk in uploaded_file.chunks():
                written += len(chunk)
                if written > limits.max_upload_bytes:
                    raise UnsafeImportFile("上传文件超过大小限制。")
                digest.update(chunk)
                output.write(chunk)
        if written == 0:
            raise UnsafeImportFile("上传文件为空。")
        return StoredUpload(path, original_filename, digest.hexdigest())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _validated_member_name(info: zipfile.ZipInfo, seen: set[str]) -> str:
    normalized = info.filename.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or not path.name:
        raise UnsafeImportFile("ZIP 包含不安全路径。")
    folded = normalized.casefold()
    if folded in seen:
        raise UnsafeImportFile("ZIP 包含重复文件名。")
    seen.add(folded)
    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode):
        raise UnsafeImportFile("ZIP 不允许符号链接。")
    if info.flag_bits & 0x1:
        raise UnsafeImportFile("不支持加密 ZIP。")
    return normalized


def _inspect_archive(
    archive: zipfile.ZipFile, *, max_files: int, max_bytes: int
) -> list[zipfile.ZipInfo]:
    seen: set[str] = set()
    files: list[zipfile.ZipInfo] = []
    total = 0
    for info in archive.infolist():
        _validated_member_name(info, seen)
        if info.is_dir():
            continue
        files.append(info)
        total += info.file_size
        if len(files) > max_files:
            raise UnsafeImportFile("ZIP 文件数量超过限制。")
        if total > max_bytes:
            raise UnsafeImportFile("ZIP 解压后大小超过限制。")
        if info.file_size and (
            not info.compress_size or info.file_size / info.compress_size > 1000
        ):
            raise UnsafeImportFile("ZIP 压缩比例异常。")
    return files


def _validate_xlsx(path: Path, *, limits: FileLimits) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            files = _inspect_archive(
                archive,
                max_files=10000,
                max_bytes=limits.max_extracted_bytes,
            )
            names = {info.filename.replace("\\", "/") for info in files}
            if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
                raise UnsafeImportFile("XLSX 内容结构无效。")
            if any(name.lower().endswith("vbaproject.bin") for name in names):
                raise UnsafeImportFile("不支持包含宏的工作簿。")
    except zipfile.BadZipFile as error:
        raise UnsafeImportFile("XLSX 文件结构无效。") from error


def prepare_bill_file(
    stored: StoredUpload, *, limits: FileLimits | None = None
) -> PreparedBillFile:
    limits = limits or FileLimits.from_settings()
    suffix = Path(stored.original_filename).suffix.lower()
    with stored.path.open("rb") as source:
        signature = source.read(8)
    is_zip = signature.startswith(b"PK\x03\x04")
    if suffix == ".csv":
        if is_zip or signature.startswith((b"%PDF", b"MZ")):
            raise UnsafeImportFile("CSV 扩展名与文件内容不匹配。")
        return PreparedBillFile(stored.path)
    if suffix == ".xlsx":
        if not is_zip:
            raise UnsafeImportFile("XLSX 扩展名与文件内容不匹配。")
        _validate_xlsx(stored.path, limits=limits)
        return PreparedBillFile(stored.path)
    if suffix != ".zip" or not is_zip:
        raise UnsafeImportFile("ZIP 扩展名与文件内容不匹配。")

    try:
        with zipfile.ZipFile(stored.path) as archive:
            files = _inspect_archive(
                archive,
                max_files=limits.max_zip_files,
                max_bytes=limits.max_extracted_bytes,
            )
            if any(Path(info.filename).suffix.lower() == ".zip" for info in files):
                raise UnsafeImportFile("不支持嵌套 ZIP。")
            candidates = [
                info for info in files if Path(info.filename).suffix.lower() in {".csv", ".xlsx"}
            ]
            if len(candidates) != 1:
                raise UnsafeImportFile("ZIP 必须且只能包含一个 CSV 或 XLSX 账单。")
            candidate = candidates[0]
            suffix = Path(candidate.filename).suffix.lower()
            descriptor, extracted_name = tempfile.mkstemp(
                prefix="extracted-", suffix=suffix, dir=import_temp_root()
            )
            extracted = Path(extracted_name)
            try:
                with os.fdopen(descriptor, "wb") as output, archive.open(candidate) as source:
                    extracted_bytes = 0
                    while chunk := source.read(1024 * 1024):
                        extracted_bytes += len(chunk)
                        if extracted_bytes > limits.max_extracted_bytes:
                            raise UnsafeImportFile("ZIP 解压后大小超过限制。")
                        output.write(chunk)
                if suffix == ".xlsx":
                    _validate_xlsx(extracted, limits=limits)
                else:
                    with extracted.open("rb") as source:
                        if source.read(4).startswith(b"PK\x03\x04"):
                            raise UnsafeImportFile("ZIP 内的 CSV 实际为压缩文件。")
                return PreparedBillFile(extracted, (extracted,))
            except Exception:
                extracted.unlink(missing_ok=True)
                raise
    except zipfile.BadZipFile as error:
        raise UnsafeImportFile("ZIP 文件结构无效。") from error


def safe_delete(path: Path) -> bool:
    root = import_temp_root()
    resolved = Path(path).resolve()
    if resolved == root or root not in resolved.parents:
        return False
    resolved.unlink(missing_ok=True)
    return True
