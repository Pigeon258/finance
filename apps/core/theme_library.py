from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
import tempfile
import unicodedata
import uuid
import warnings
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.db import transaction
from fontTools.ttLib import TTFont, TTLibError
from PIL import Image, UnidentifiedImageError

from .models import SystemPreference
from .themes import ThemeDescriptor, ThemeValidationError, get_theme_registry, load_theme

logger = logging.getLogger("personal_finance.theme")

_THEME_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?$")
_IMAGE_FORMATS = {".jpeg": "JPEG", ".jpg": "JPEG", ".png": "PNG", ".webp": "WEBP"}
_TEXT_SUFFIXES = frozenset({".css", ".json", ".txt"})
_ALLOWED_COMPRESSION = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})


class ThemeLibraryError(ValueError):
    def __init__(self, message: str, *, code: str = "invalid-package") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ThemeInstallResult:
    theme: ThemeDescriptor
    status: str


@dataclass(frozen=True)
class ArchiveMember:
    info: zipfile.ZipInfo
    relative_path: PurePosixPath


def _audit(operation: str, result: str, *, theme_id: str = "unknown", reason: str = "") -> None:
    # 审计日志只记录规范化主题 ID 和分类结果，不记录上传文件名、路径或主题内容。
    logger.info(
        "theme_operation operation=%s result=%s theme_id=%s reason=%s",
        operation,
        result,
        theme_id if _THEME_ID_RE.fullmatch(theme_id) else "unknown",
        reason,
    )


def _normalized_zip_path(value: str) -> PurePosixPath:
    if not value or "\x00" in value or "\\" in value:
        raise ThemeLibraryError("ZIP 包含无效文件路径。", code="invalid-path")
    normalized = unicodedata.normalize("NFC", value)
    if normalized != value:
        raise ThemeLibraryError("ZIP 文件名必须使用规范化 Unicode。", code="invalid-path")
    path = PurePosixPath(normalized.rstrip("/"))
    if (
        not path.parts
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or ":" in path.parts[0]
    ):
        raise ThemeLibraryError("ZIP 路径不得越过主题目录。", code="path-traversal")
    return path


def _reject_special_member(info: zipfile.ZipInfo) -> None:
    if info.flag_bits & 0x1:
        raise ThemeLibraryError("不允许加密 ZIP 条目。", code="encrypted-entry")
    if info.compress_type not in _ALLOWED_COMPRESSION:
        raise ThemeLibraryError("ZIP 使用了不允许的压缩算法。", code="compression-method")
    mode = (info.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(mode)
    if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
        raise ThemeLibraryError("ZIP 不允许符号链接或特殊文件。", code="special-file")


def _archive_members(bundle: zipfile.ZipFile) -> tuple[list[ArchiveMember], zipfile.ZipInfo]:
    files: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
    seen: set[str] = set()
    total_size = 0
    for info in bundle.infolist():
        _reject_special_member(info)
        path = _normalized_zip_path(info.filename)
        if info.is_dir():
            continue
        identity = path.as_posix().casefold()
        if identity in seen:
            raise ThemeLibraryError("ZIP 包含重复或大小写冲突路径。", code="duplicate-path")
        seen.add(identity)
        if len(files) >= settings.THEME_IMPORT_MAX_FILES:
            raise ThemeLibraryError("ZIP 文件数量超过限制。", code="too-many-files")
        total_size += info.file_size
        if total_size > settings.THEME_IMPORT_MAX_EXTRACTED_BYTES:
            raise ThemeLibraryError("ZIP 解压后总大小超过限制。", code="zip-bomb")
        if info.file_size > 0:
            if info.compress_size <= 0:
                raise ThemeLibraryError("ZIP 压缩比例异常。", code="zip-bomb")
            ratio = info.file_size / info.compress_size
            if ratio > settings.THEME_IMPORT_MAX_COMPRESSION_RATIO:
                raise ThemeLibraryError("ZIP 压缩比例超过限制。", code="zip-bomb")
        files.append((info, path))

    if not files:
        raise ThemeLibraryError("ZIP 主题包为空。", code="empty-archive")
    direct_manifest = [item for item in files if item[1].as_posix() == "manifest.json"]
    wrapped_manifest = [
        item for item in files if len(item[1].parts) == 2 and item[1].name == "manifest.json"
    ]
    if direct_manifest:
        if len(direct_manifest) != 1:
            raise ThemeLibraryError("ZIP 清单不唯一。", code="manifest-layout")
        prefix: tuple[str, ...] = ()
        manifest_info = direct_manifest[0][0]
    elif len(wrapped_manifest) == 1:
        prefix = (wrapped_manifest[0][1].parts[0],)
        if any(path.parts[:1] != prefix or len(path.parts) < 2 for _, path in files):
            raise ThemeLibraryError("ZIP 只能包含一个顶级主题目录。", code="nested-package")
        manifest_info = wrapped_manifest[0][0]
    else:
        raise ThemeLibraryError("ZIP 根目录包装层级无效。", code="manifest-layout")

    members = [
        ArchiveMember(info=info, relative_path=PurePosixPath(*path.parts[len(prefix) :]))
        for info, path in files
    ]
    if any(not member.relative_path.parts for member in members):
        raise ThemeLibraryError("ZIP 文件布局无效。", code="manifest-layout")
    return members, manifest_info


def _manifest_theme_id(bundle: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    if info.file_size > 256 * 1024:
        raise ThemeLibraryError("manifest.json 超过允许大小。", code="manifest-size")
    try:
        manifest = json.loads(bundle.read(info).decode("utf-8"))
    except (KeyError, RuntimeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ThemeLibraryError(
            "manifest.json 不是有效的 UTF-8 JSON。", code="manifest-json"
        ) from error
    theme_id = manifest.get("id") if isinstance(manifest, dict) else None
    if not isinstance(theme_id, str) or not _THEME_ID_RE.fullmatch(theme_id):
        raise ThemeLibraryError("主题 ID 无效。", code="theme-id")
    return theme_id


def _extract_members(
    bundle: zipfile.ZipFile, members: list[ArchiveMember], destination: Path
) -> None:
    destination.mkdir(mode=0o700)
    for member in members:
        target = destination.joinpath(*member.relative_path.parts)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            with bundle.open(member.info, "r") as source, target.open("xb") as output:
                remaining = member.info.file_size
                while remaining:
                    chunk = source.read(min(64 * 1024, remaining))
                    if not chunk:
                        raise ThemeLibraryError("ZIP 条目提前结束。", code="truncated-entry")
                    output.write(chunk)
                    remaining -= len(chunk)
                if source.read(1):
                    raise ThemeLibraryError("ZIP 条目大小与目录不一致。", code="entry-size")
        except (OSError, RuntimeError, zipfile.BadZipFile) as error:
            if isinstance(error, ThemeLibraryError):
                raise
            raise ThemeLibraryError("ZIP 解压失败。", code="extract-failed") from error


def _validate_image(path: Path) -> None:
    expected_format = _IMAGE_FORMATS[path.suffix.lower()]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                if image.format != expected_format or getattr(image, "n_frames", 1) != 1:
                    raise ThemeLibraryError(
                        "图片实际格式与扩展名不符或包含动画。", code="image-type"
                    )
                width, height = image.size
                if width <= 0 or height <= 0 or width * height > settings.THEME_IMAGE_MAX_PIXELS:
                    raise ThemeLibraryError("图片像素尺寸超过限制。", code="image-pixels")
                image.verify()
            with Image.open(path) as image:
                image.load()
    except ThemeLibraryError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
    ) as error:
        raise ThemeLibraryError("图片内容校验失败。", code="image-invalid") from error


def _validate_font(path: Path) -> None:
    try:
        with TTFont(path, lazy=False, recalcBBoxes=False, recalcTimestamp=False) as font:
            if font.flavor != "woff2" or not {"cmap", "head", "maxp", "name"} <= set(font.keys()):
                raise ThemeLibraryError("WOFF2 字体结构不完整。", code="font-invalid")
    except ThemeLibraryError:
        raise
    except (OSError, TTLibError) as error:
        raise ThemeLibraryError("WOFF2 字体内容校验失败。", code="font-invalid") from error


def _deep_validate_files(root: Path) -> None:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in _TEXT_SUFFIXES:
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as error:
                raise ThemeLibraryError(
                    "主题文本资源必须是 UTF-8。", code="text-encoding"
                ) from error
            if "\x00" in content:
                raise ThemeLibraryError("主题文本资源包含空字节。", code="text-encoding")
        elif suffix in _IMAGE_FORMATS:
            _validate_image(path)
        elif suffix == ".woff2":
            _validate_font(path)


def _prepare_public_permissions(root: Path) -> None:
    # 主题资源不含敏感数据，需要允许独立静态文件服务只读访问。
    for path in root.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    root.chmod(0o755)


def _publish_theme(staging_root: Path, final_root: Path) -> None:
    os.replace(staging_root, final_root)


def install_theme_zip(uploaded_file) -> ThemeInstallResult:
    theme_id = "unknown"
    try:
        if uploaded_file.size > settings.THEME_IMPORT_MAX_UPLOAD_BYTES:
            raise ThemeLibraryError("ZIP 上传文件超过限制。", code="upload-size")
        temp_parent = Path(settings.THEME_IMPORT_TMP_DIR)
        temp_parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        runtime_root = Path(settings.THEME_RUNTIME_DIR)
        runtime_root.mkdir(parents=True, exist_ok=True, mode=0o755)
        with tempfile.TemporaryDirectory(prefix="pf-theme-", dir=temp_parent) as temp_name:
            archive_path = Path(temp_name) / "upload.zip"
            written = 0
            with archive_path.open("xb") as output:
                for chunk in uploaded_file.chunks():
                    written += len(chunk)
                    if written > settings.THEME_IMPORT_MAX_UPLOAD_BYTES:
                        raise ThemeLibraryError("ZIP 上传文件超过限制。", code="upload-size")
                    output.write(chunk)
            try:
                with zipfile.ZipFile(archive_path) as bundle:
                    members, manifest_info = _archive_members(bundle)
                    theme_id = _manifest_theme_id(bundle, manifest_info)
                    staging_root = Path(temp_name) / theme_id
                    _extract_members(bundle, members, staging_root)
            except zipfile.BadZipFile as error:
                raise ThemeLibraryError("上传文件不是有效 ZIP。", code="bad-zip") from error

            _deep_validate_files(staging_root)
            try:
                theme = load_theme(
                    staging_root,
                    source="runtime",
                    public_base_url=f"{str(settings.THEME_RUNTIME_URL).rstrip('/')}/{theme_id}",
                )
            except (OSError, ThemeValidationError) as error:
                raise ThemeLibraryError(str(error), code="theme-validation") from error

            registry_theme = get_theme_registry().get(theme_id)
            final_root = runtime_root / theme_id
            if registry_theme is not None:
                if registry_theme.source == "runtime" and registry_theme.revision == theme.revision:
                    _audit("install", "already-installed", theme_id=theme_id)
                    return ThemeInstallResult(registry_theme, "already-installed")
                raise ThemeLibraryError(
                    "主题 ID 已存在且内容不同，未覆盖原主题。", code="id-conflict"
                )
            if final_root.exists():
                raise ThemeLibraryError(
                    "主题目录已存在但未通过注册，拒绝覆盖。", code="id-conflict"
                )
            _prepare_public_permissions(staging_root)
            try:
                _publish_theme(staging_root, final_root)
            except OSError as error:
                raise ThemeLibraryError(
                    "主题原子安装失败，未留下部分目录。", code="publish-failed"
                ) from error
            installed = get_theme_registry().get(theme_id)
            if installed is None:
                if final_root.exists():
                    shutil.rmtree(final_root)
                raise ThemeLibraryError("主题安装后注册校验失败。", code="post-install-validation")
            _audit("install", "succeeded", theme_id=theme_id)
            return ThemeInstallResult(installed, "installed")
    except ThemeLibraryError as error:
        _audit("install", "failed", theme_id=theme_id, reason=error.code)
        raise


@transaction.atomic
def activate_theme(theme_id: str) -> ThemeDescriptor:
    theme = get_theme_registry().get(theme_id)
    if theme is None:
        _audit("activate", "failed", theme_id=theme_id, reason="not-registered")
        raise ThemeLibraryError("主题当前不可用，已保留原活动主题。", code="not-registered")
    if theme.root is not None:
        try:
            revalidated = load_theme(
                theme.root,
                source=theme.source,
                public_base_url=theme.stylesheet_url.rsplit("/", 1)[0],
            )
        except (OSError, ThemeValidationError) as error:
            _audit("activate", "failed", theme_id=theme_id, reason="smoke-validation")
            raise ThemeLibraryError(
                "主题启用前冒烟校验失败，已保留原活动主题。", code="smoke-validation"
            ) from error
        if revalidated.revision != theme.revision:
            raise ThemeLibraryError(
                "主题内容在启用前发生变化，已保留原活动主题。", code="revision-changed"
            )
    preference, _ = SystemPreference.objects.select_for_update().get_or_create(
        pk=SystemPreference.SINGLETON_ID
    )
    preference.active_theme_id = theme.id
    preference.last_known_good_theme_id = theme.id
    preference.save(update_fields=["active_theme_id", "last_known_good_theme_id", "updated_at"])
    _audit("activate", "succeeded", theme_id=theme.id)
    return theme


def restore_safe_default() -> ThemeDescriptor:
    return activate_theme("safe-default")


def delete_theme(theme_id: str, *, preview_theme_id: str = "") -> None:
    theme = get_theme_registry().get(theme_id)
    if theme is None:
        raise ThemeLibraryError("主题不存在。", code="not-registered")
    if theme.source != "runtime" or theme.root is None:
        raise ThemeLibraryError("内置主题不可删除。", code="builtin-protected")
    if preview_theme_id == theme_id:
        raise ThemeLibraryError("正在预览的主题不可删除，请先结束预览。", code="preview-protected")
    with transaction.atomic():
        preference = SystemPreference.objects.select_for_update().get(
            pk=SystemPreference.SINGLETON_ID
        )
        if theme_id in {preference.active_theme_id, preference.last_known_good_theme_id}:
            raise ThemeLibraryError(
                "活动或 last-known-good 主题不可删除。", code="active-protected"
            )

        runtime_root = Path(settings.THEME_RUNTIME_DIR).resolve()
        theme_root = theme.root.resolve()
        if theme_root.parent != runtime_root or theme_root.name != theme_id:
            raise ThemeLibraryError("主题目录不在受控运行时目录。", code="path-boundary")
        quarantine = runtime_root / f".delete-{uuid.uuid4().hex}"
        try:
            os.replace(theme_root, quarantine)
            shutil.rmtree(quarantine)
        except OSError as error:
            if quarantine.exists() and not theme_root.exists():
                os.replace(quarantine, theme_root)
            _audit("delete", "failed", theme_id=theme_id, reason="filesystem")
            raise ThemeLibraryError("主题删除失败，原目录已恢复。", code="delete-failed") from error
    _audit("delete", "succeeded", theme_id=theme_id)
