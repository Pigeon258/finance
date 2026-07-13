import io
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.imports.file_safety import (
    FileLimits,
    UnsafeImportFile,
    _inspect_archive,
    prepare_bill_file,
    safe_delete,
    store_uploaded_file,
)


@pytest.fixture(autouse=True)
def import_tmp(settings, tmp_path):
    settings.IMPORT_TMP_DIR = tmp_path / "imports"


def _upload(name: str, content: bytes, content_type="application/octet-stream"):
    return SimpleUploadedFile(name, content, content_type=content_type)


def _zip_bytes(files: list[tuple[str | zipfile.ZipInfo, bytes]]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files:
            archive.writestr(name, content)
    return output.getvalue()


def test_upload_size_boundary_is_inclusive():
    content = b"a,b\n1,2\n"
    limits = FileLimits(len(content), 100, 20)
    stored = store_uploaded_file(_upload("bill.csv", content), limits=limits)
    assert stored.path.exists()
    assert safe_delete(stored.path)
    with pytest.raises(UnsafeImportFile, match="大小限制"):
        store_uploaded_file(_upload("bill.csv", content + b"x"), limits=limits)


def test_extracted_size_and_file_count_boundaries_are_inclusive():
    csv_content = b"a,b\n1,2\n"
    archive = _zip_bytes([("bill.csv", csv_content), ("readme.txt", b"x")])
    stored = store_uploaded_file(
        _upload("bill.zip", archive), limits=FileLimits(len(archive), 100, 2)
    )
    prepared = prepare_bill_file(stored, limits=FileLimits(len(archive), len(csv_content) + 1, 2))
    assert prepared.path.read_bytes() == csv_content
    for path in prepared.cleanup_paths:
        safe_delete(path)
    safe_delete(stored.path)

    stored = store_uploaded_file(
        _upload("bill.zip", archive), limits=FileLimits(len(archive), 100, 2)
    )
    with pytest.raises(UnsafeImportFile, match="解压后大小"):
        prepare_bill_file(stored, limits=FileLimits(len(archive), len(csv_content), 2))
    safe_delete(stored.path)


def test_zip_rejects_more_than_maximum_file_count():
    archive = _zip_bytes([("bill.csv", b"a,b\n1,2\n"), ("readme.txt", b"x")])
    stored = store_uploaded_file(_upload("bill.zip", archive))
    with pytest.raises(UnsafeImportFile, match="文件数量"):
        prepare_bill_file(stored, limits=FileLimits(len(archive), 100, 1))
    safe_delete(stored.path)


def test_zip_rejects_encrypted_members():
    info = zipfile.ZipInfo("bill.csv")
    info.flag_bits = 0x1

    class Archive:
        @staticmethod
        def infolist():
            return [info]

    with pytest.raises(UnsafeImportFile, match="加密 ZIP"):
        _inspect_archive(Archive(), max_files=20, max_bytes=100)


@pytest.mark.parametrize(
    ("files", "message"),
    [
        ([("../bill.csv", b"a,b\n1,2")], "不安全路径"),
        ([("bill.zip", b"PK\x03\x04")], "嵌套 ZIP"),
        ([("one.csv", b"a"), ("two.csv", b"b")], "只能包含一个"),
    ],
)
def test_zip_rejects_traversal_nested_and_multiple_bills(files, message):
    archive = _zip_bytes(files)
    stored = store_uploaded_file(_upload("bill.zip", archive))
    with pytest.raises(UnsafeImportFile, match=message):
        prepare_bill_file(stored)
    safe_delete(stored.path)


def test_zip_rejects_symlink():
    link = zipfile.ZipInfo("bill.csv")
    link.create_system = 3
    link.external_attr = (0o120777 << 16) | 0xA000
    archive = _zip_bytes([(link, b"target")])
    stored = store_uploaded_file(_upload("bill.zip", archive))
    with pytest.raises(UnsafeImportFile, match="符号链接"):
        prepare_bill_file(stored)
    safe_delete(stored.path)


def test_fake_extensions_and_unsafe_names_are_rejected():
    class UnsafeUpload:
        name = "../bill.csv"
        content_type = "text/csv"

        @staticmethod
        def chunks():
            yield b"a,b"

    with pytest.raises(UnsafeImportFile, match="文件名不安全"):
        store_uploaded_file(UnsafeUpload())
    stored = store_uploaded_file(_upload("bill.csv", b"PK\x03\x04fake"))
    with pytest.raises(UnsafeImportFile, match="扩展名"):
        prepare_bill_file(stored)
    safe_delete(stored.path)


def test_xlsx_with_macros_is_rejected():
    archive = _zip_bytes(
        [
            ("[Content_Types].xml", b"<Types/>"),
            ("xl/workbook.xml", b"<workbook/>"),
            ("xl/vbaProject.bin", b"macro"),
        ]
    )
    stored = store_uploaded_file(_upload("bill.xlsx", archive))
    with pytest.raises(UnsafeImportFile, match="宏"):
        prepare_bill_file(stored)
    safe_delete(stored.path)


def test_safe_delete_never_deletes_outside_import_root(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    assert safe_delete(outside) is False
    assert outside.exists()
