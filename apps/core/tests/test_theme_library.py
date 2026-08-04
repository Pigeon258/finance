import io
import logging
import stat
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.exceptions import SuspiciousFileOperation
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404
from django.test import Client
from django.test.client import RequestFactory
from django.urls import reverse
from django.views.static import serve
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from PIL import Image

from apps.core.forms import SystemPreferenceForm
from apps.core.models import SystemPreference
from apps.core.tests.test_theme_runtime import _write_theme
from apps.core.theme_library import (
    ThemeLibraryError,
    activate_theme,
    delete_theme,
    install_theme_zip,
    restore_safe_default,
)
from apps.core.themes import get_theme_registry

PASSWORD = "correct horse battery staple"


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_superuser(username="owner", password=PASSWORD)


@pytest.fixture
def theme_directories(tmp_path, settings):
    settings.THEME_RUNTIME_DIR = tmp_path / "runtime"
    settings.THEME_IMPORT_TMP_DIR = tmp_path / "imports"
    return tmp_path


def _zip_directory(root: Path, *, prefix: str = "") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                relative = path.relative_to(root).as_posix()
                bundle.write(path, f"{prefix}/{relative}".lstrip("/"))
    return output.getvalue()


def _upload(content: bytes, *, name: str = "theme.zip") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content, content_type="application/zip")


def _webp_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (32, 18), "#3157d5").save(output, "WEBP")
    return output.getvalue()


def _woff2_bytes() -> bytes:
    builder = FontBuilder(1024, isTTF=True)
    glyph_order = [".notdef", "space"]
    builder.setupGlyphOrder(glyph_order)
    builder.setupCharacterMap({32: "space"})
    glyphs = {}
    for name in glyph_order:
        pen = TTGlyphPen(None)
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name: (500, 0) for name in glyph_order})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable(
        {
            "familyName": "Theme Test",
            "styleName": "Regular",
            "uniqueFontIdentifier": "Theme Test Regular",
            "fullName": "Theme Test Regular",
            "psName": "ThemeTest-Regular",
        }
    )
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    builder.setupMaxp()
    builder.font.flavor = "woff2"
    output = io.BytesIO()
    builder.font.save(output)
    return output.getvalue()


@pytest.mark.django_db
def test_valid_zip_installs_atomically_without_activating_and_duplicate_is_idempotent(
    theme_directories,
):
    root = _write_theme(theme_directories / "source", theme_id="local-night")
    archive = _zip_directory(root, prefix="untrusted-wrapper")
    before = SystemPreference.objects.get().active_theme_id

    installed = install_theme_zip(_upload(archive))
    duplicate = install_theme_zip(_upload(archive))

    assert installed.status == "installed"
    assert duplicate.status == "already-installed"
    assert installed.theme.id == "local-night"
    assert (theme_directories / "runtime" / "local-night" / "manifest.json").is_file()
    assert SystemPreference.objects.get().active_theme_id == before


@pytest.mark.django_db
def test_valid_image_and_woff2_assets_receive_deep_validation(theme_directories):
    root = _write_theme(
        theme_directories / "source",
        theme_id="asset-theme",
        extra_files={
            "assets/font.woff2": _woff2_bytes(),
            "preview.webp": _webp_bytes(),
        },
    )

    result = install_theme_zip(_upload(_zip_directory(root)))

    assert result.status == "installed"
    assert get_theme_registry().get("asset-theme") is not None


@pytest.mark.django_db
def test_same_id_with_different_content_never_overwrites(theme_directories):
    first = _write_theme(theme_directories / "first", theme_id="conflict-theme")
    second = _write_theme(
        theme_directories / "second",
        theme_id="conflict-theme",
        css='[data-pf-part="app-shell"] { --pf-accent: #ff8398; }',
    )
    install_theme_zip(_upload(_zip_directory(first)))
    original = (theme_directories / "runtime" / "conflict-theme" / "theme.css").read_bytes()

    with pytest.raises(ThemeLibraryError, match="内容不同"):
        install_theme_zip(_upload(_zip_directory(second)))

    assert (theme_directories / "runtime" / "conflict-theme" / "theme.css").read_bytes() == original


@pytest.mark.parametrize(
    ("entry_name", "code"),
    [
        ("../escape.txt", "path-traversal"),
        ("C:/escape.txt", "path-traversal"),
        ("outer/inner/manifest.json", "manifest-layout"),
    ],
)
def test_zip_traversal_drive_and_nested_wrappers_are_rejected(tmp_path, settings, entry_name, code):
    settings.THEME_RUNTIME_DIR = tmp_path / "runtime"
    settings.THEME_IMPORT_TMP_DIR = tmp_path / "imports"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(entry_name, "{}")

    with pytest.raises(ThemeLibraryError) as error:
        install_theme_zip(_upload(output.getvalue()))

    assert error.value.code == code
    assert not (tmp_path / "escape.txt").exists()


def test_zip_symlink_is_rejected_before_extraction(theme_directories):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        link = zipfile.ZipInfo("theme/link.txt")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        bundle.writestr(link, "../target")

    with pytest.raises(ThemeLibraryError) as error:
        install_theme_zip(_upload(output.getvalue()))

    assert error.value.code == "special-file"


def test_zip_bomb_ratio_and_file_count_limits_are_rejected(theme_directories, settings):
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("manifest.json", "{}")
        bundle.writestr("assets/repeated.txt", b"0" * 1_000_000)
    with pytest.raises(ThemeLibraryError) as ratio_error:
        install_theme_zip(_upload(bomb.getvalue()))
    assert ratio_error.value.code == "zip-bomb"

    settings.THEME_IMPORT_MAX_FILES = 1
    many = io.BytesIO()
    with zipfile.ZipFile(many, "w") as bundle:
        bundle.writestr("manifest.json", "{}")
        bundle.writestr("theme.json", "{}")
    with pytest.raises(ThemeLibraryError) as count_error:
        install_theme_zip(_upload(many.getvalue()))
    assert count_error.value.code == "too-many-files"


@pytest.mark.parametrize(
    ("path", "content", "code"),
    [
        ("preview.webp", b"not-an-image", "image-invalid"),
        ("assets/font.woff2", b"not-a-font", "font-invalid"),
    ],
)
def test_forged_image_and_font_types_are_rejected(theme_directories, path, content, code):
    root = _write_theme(
        theme_directories / "source",
        theme_id="forged-assets",
        extra_files={path: content},
    )

    with pytest.raises(ThemeLibraryError) as error:
        install_theme_zip(_upload(_zip_directory(root)))

    assert error.value.code == code
    assert not (theme_directories / "runtime" / "forged-assets").exists()


def test_executable_theme_payload_is_rejected(theme_directories):
    root = _write_theme(
        theme_directories / "source",
        theme_id="script-theme",
        extra_files={"assets/payload.js": b"alert(1)"},
    )

    with pytest.raises(ThemeLibraryError, match="文件类型不受支持"):
        install_theme_zip(_upload(_zip_directory(root)))

    assert not (theme_directories / "runtime" / "script-theme").exists()


def test_publish_failure_leaves_no_partial_theme(theme_directories):
    root = _write_theme(theme_directories / "source", theme_id="interrupted-theme")

    with (
        patch("apps.core.theme_library._publish_theme", side_effect=OSError("disk full")),
        pytest.raises(ThemeLibraryError) as error,
    ):
        install_theme_zip(_upload(_zip_directory(root)))

    assert error.value.code == "publish-failed"
    assert not (theme_directories / "runtime" / "interrupted-theme").exists()


@pytest.mark.django_db
def test_activation_updates_last_good_uses_row_lock_and_failure_rolls_back(theme_directories):
    root = _write_theme(theme_directories / "source", theme_id="switch-theme")
    install_theme_zip(_upload(_zip_directory(root)))

    with patch.object(
        SystemPreference.objects,
        "select_for_update",
        wraps=SystemPreference.objects.select_for_update,
    ) as row_lock:
        activate_theme("switch-theme")
    preference = SystemPreference.objects.get()
    assert row_lock.called
    assert preference.active_theme_id == "switch-theme"
    assert preference.last_known_good_theme_id == "switch-theme"

    with (
        patch.object(SystemPreference, "save", side_effect=RuntimeError("write failed")),
        pytest.raises(RuntimeError, match="write failed"),
    ):
        activate_theme("safe-default")
    preference.refresh_from_db()
    assert preference.active_theme_id == "switch-theme"
    assert preference.last_known_good_theme_id == "switch-theme"


@pytest.mark.django_db
def test_activation_revalidates_changed_files_before_writing_last_good(theme_directories):
    root = _write_theme(theme_directories / "source", theme_id="changed-theme")
    install_theme_zip(_upload(_zip_directory(root)))
    preference = SystemPreference.objects.get()
    original = (preference.active_theme_id, preference.last_known_good_theme_id)
    (theme_directories / "runtime" / "changed-theme" / "theme.css").write_text(
        "changed after install", encoding="utf-8"
    )

    with pytest.raises(ThemeLibraryError) as error:
        activate_theme("changed-theme")

    assert error.value.code == "not-registered"
    preference.refresh_from_db()
    assert (preference.active_theme_id, preference.last_known_good_theme_id) == original

@pytest.mark.django_db
def test_delete_protects_builtin_active_last_good_and_preview_then_allows_safe_delete(
    theme_directories,
):
    root = _write_theme(theme_directories / "source", theme_id="delete-theme")
    install_theme_zip(_upload(_zip_directory(root)))

    with pytest.raises(ThemeLibraryError) as builtin:
        delete_theme("safe-default")
    assert builtin.value.code == "builtin-protected"

    activate_theme("delete-theme")
    with pytest.raises(ThemeLibraryError) as active:
        delete_theme("delete-theme")
    assert active.value.code == "active-protected"

    restore_safe_default()
    with pytest.raises(ThemeLibraryError) as preview:
        delete_theme("delete-theme", preview_theme_id="delete-theme")
    assert preview.value.code == "preview-protected"

    delete_theme("delete-theme")
    assert not (theme_directories / "runtime" / "delete-theme").exists()


@pytest.mark.django_db
def test_delete_failure_restores_original_directory(theme_directories):
    root = _write_theme(theme_directories / "source", theme_id="restore-on-delete")
    install_theme_zip(_upload(_zip_directory(root)))
    final_root = theme_directories / "runtime" / "restore-on-delete"

    with (
        patch("apps.core.theme_library.shutil.rmtree", side_effect=OSError("locked")),
        pytest.raises(ThemeLibraryError) as error,
    ):
        delete_theme("restore-on-delete")

    assert error.value.code == "delete-failed"
    assert final_root.is_dir()
    assert (final_root / "manifest.json").is_file()


@pytest.mark.django_db
def test_general_settings_form_cannot_bypass_explicit_theme_activation():
    preference = SystemPreference.objects.get()
    original_theme = preference.active_theme_id
    form = SystemPreferenceForm(
        {
            "time_zone": "Asia/Shanghai",
            "category_warning_threshold": "80.00",
            "category_over_budget_threshold": "100.00",
            "large_expense_threshold": "500.00",
            "login_failure_window_minutes": 15,
            "login_failure_ip_limit": 5,
            "login_failure_global_limit": 20,
            "session_idle_timeout_minutes": 60,
            "session_absolute_timeout_hours": 24,
            "active_theme_id": "safe-default",
            "appearance_mode": SystemPreference.AppearanceMode.AUTO,
            "reduce_motion": False,
            "show_theme_background": True,
        },
        instance=preference,
    )

    assert form.is_valid(), form.errors
    assert form.save().active_theme_id == original_theme


@pytest.mark.django_db
def test_preview_is_session_scoped_expires_and_does_not_promote_last_good(
    client, owner, theme_directories
):
    root = _write_theme(theme_directories / "source", theme_id="preview-theme")
    install_theme_zip(_upload(_zip_directory(root)))
    client.force_login(owner)
    preference = SystemPreference.objects.get()
    original_active = preference.active_theme_id
    original_last_good = preference.last_known_good_theme_id

    response = client.post(reverse("core:theme-preview", args=["preview-theme"]))
    assert response.status_code == 302
    previewed = client.get(reverse("core:home"))
    assert 'data-theme-id="preview-theme"' in previewed.content.decode()
    assert "临时预览" in previewed.content.decode()
    preference.refresh_from_db()
    assert preference.active_theme_id == original_active
    assert preference.last_known_good_theme_id == original_last_good

    session = client.session
    session["theme_preview"] = {"id": "preview-theme", "expires_at": 0}
    session.save()
    expired = client.get(reverse("core:home"))
    assert f'data-theme-id="{original_active}"' in expired.content.decode()


@pytest.mark.django_db
def test_theme_library_requires_login_csrf_and_never_logs_upload_name_or_path(
    owner, theme_directories, caplog
):
    anonymous = Client()
    assert anonymous.get(reverse("core:theme-library")).status_code == 302

    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(owner)
    assert csrf_client.post(reverse("core:theme-restore-safe")).status_code == 403

    root = _write_theme(theme_directories / "source", theme_id="audit-theme")
    upload_name = "private-user-path-theme.zip"
    with caplog.at_level(logging.INFO, logger="personal_finance.theme"):
        install_theme_zip(_upload(_zip_directory(root), name=upload_name))
    logs = caplog.text
    assert "theme_id=audit-theme" in logs
    assert upload_name not in logs
    assert str(theme_directories) not in logs


@pytest.mark.django_db
def test_theme_library_page_lists_builtins_and_runtime_theme(client, owner, theme_directories):
    root = _write_theme(
        theme_directories / "source",
        theme_id="listed-theme",
        extra_files={"preview.webp": _webp_bytes()},
    )
    install_theme_zip(_upload(_zip_directory(root)))
    client.force_login(owner)

    response = client.get(reverse("core:theme-library"))
    content = response.content.decode()

    assert response.status_code == 200
    assert "Aurora Ledger 极光账境" in content
    assert "安全默认主题" in content
    assert "测试主题" in content
    assert "/themes/listed-theme/preview.webp?v=" in content
    assert "script" not in get_theme_registry().get("listed-theme").capabilities


def test_runtime_static_serving_rejects_directory_traversal(theme_directories):
    secret = theme_directories / "secret.txt"
    secret.write_text("private", encoding="utf-8")
    request = RequestFactory().get("/themes/../secret.txt")

    with pytest.raises((Http404, SuspiciousFileOperation)):
        serve(request, "../secret.txt", document_root=theme_directories / "runtime")
