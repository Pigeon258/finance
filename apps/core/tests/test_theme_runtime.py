import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from django.db import DatabaseError
from django.test import override_settings

from apps.core import backup
from apps.core.context_processors import theme_context
from apps.core.models import SystemPreference
from apps.core.themes import (
    ThemeRegistry,
    ThemeValidationError,
    get_theme_registry,
    load_theme,
    validate_safe_css,
)

BACKUP_PASSPHRASE = "theme backup passphrase"


def _theme_config(**overrides):
    config = {
        "appearance": "dark",
        "tokens": {"--pf-accent": "#6f8cff"},
        "art": {
            "asset": None,
            "focus": {"x": 0, "y": 1},
            "mode": "off",
            "overlay": "transparent",
            "safe_area": "none",
        },
        "components": {"metric-card": {"border-radius": "1rem"}},
        "charts": {"color": ["#6f8cff", "#8de3c7"]},
        "accessibility": {"high_contrast": True, "reduce_motion": "disable"},
    }
    config.update(overrides)
    return config


def _write_theme(
    parent: Path,
    *,
    theme_id: str = "test-theme",
    config=None,
    css='[data-pf-part="app-shell"] { --pf-accent: #6f8cff; color: var(--pf-text); }',
    schema_version: int = 1,
    min_app_version: str = "0.1.0",
    capabilities=None,
    extra_files=None,
):
    root = parent / theme_id
    root.mkdir(parents=True)
    files = {
        "theme.json": json.dumps(config or _theme_config(), ensure_ascii=False, indent=2).encode(),
        "theme.css": css.encode(),
    }
    files.update(extra_files or {})
    records = []
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        records.append(
            {
                "path": name,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    manifest = {
        "schema_version": schema_version,
        "id": theme_id,
        "name": "测试主题",
        "version": "1.2.3",
        "min_app_version": min_app_version,
        "capabilities": capabilities or ["tokens", "safe-css", "charts"],
        "files": records,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return root


def test_builtin_theme_uses_versioned_descriptor_and_chart_output():
    registry = get_theme_registry()
    theme = registry.get("safe-default")

    assert theme is not None
    assert theme.source == "builtin"
    assert theme.revision != "emergency"
    assert theme.cache_key.startswith("pf-theme:safe-default:1.0.0:")
    assert theme.stylesheet_url.endswith("/static/themes/safe-default/theme.css")
    assert theme.chart_theme["color"][0] == "#3157d5"
    assert registry.errors == {}


def test_manifest_and_config_accept_boundaries_and_verify_content(tmp_path):
    root = _write_theme(tmp_path)

    theme = load_theme(root, source="runtime", public_base_url="/themes/test-theme")

    assert theme.id == "test-theme"
    assert theme.appearance == "dark"
    assert theme.config["art"]["focus"] == {"x": 0, "y": 1}


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"schema_version": 2}, "格式版本"),
        ({"min_app_version": "9.0.0"}, "更高版本"),
        ({"capabilities": ["tokens", "safe-css", "charts", "python"]}, "未知必需能力"),
        (
            {
                "config": _theme_config(
                    art={
                        "asset": None,
                        "focus": {"x": -0.01, "y": 1},
                        "mode": "off",
                        "overlay": "transparent",
                        "safe_area": "none",
                    }
                )
            },
            "焦点坐标",
        ),
    ],
)
def test_manifest_and_config_fail_closed(tmp_path, kwargs, message):
    root = _write_theme(tmp_path, **kwargs)

    with pytest.raises(ThemeValidationError, match=message):
        load_theme(root, source="runtime", public_base_url="/themes/test-theme")


def test_manifest_rejects_changed_or_missing_resource(tmp_path):
    root = _write_theme(tmp_path)
    (root / "theme.css").write_text("changed", encoding="utf-8")

    with pytest.raises(ThemeValidationError, match="大小校验|哈希校验"):
        load_theme(root, source="runtime", public_base_url="/themes/test-theme")


def test_safe_css_accepts_registered_parts_and_registered_relative_asset():
    validate_safe_css(
        """
        [data-pf-part="metric-card"]:hover {
          background-image: url("assets/background.webp");
          border-color: rgb(111 140 255 / 60%);
          opacity: 0.9;
        }
        """,
        asset_paths=frozenset({"assets/background.webp"}),
    )


@pytest.mark.parametrize(
    "css",
    [
        '@import url("https://example.com/theme.css");',
        "body { color: red; }",
        '[data-pf-part="unknown"] { color: red; }',
        '[data-pf-part="metric-card"] { display: none; }',
        '[data-pf-part="metric-card"] { opacity: 0; }',
        '[data-pf-part="metric-card"] { opacity: 1.1; }',
        '[data-pf-part="metric-card"] { background: url("https://example.com/a.webp"); }',
        '[data-pf-part="metric-card"] { background: url("assets/missing.webp"); }',
        '[data-pf-part="metric-card"], body { color: red; }',
        '[data-pf-part="metric-card"] { color: red; }} body { color: transparent; }',
    ],
)
def test_safe_css_rejects_scope_property_url_and_parser_escapes(css):
    with pytest.raises(ThemeValidationError):
        validate_safe_css(css)


@override_settings(THEME_RUNTIME_DIR="missing-runtime-theme-directory")
def test_selector_uses_last_known_good_then_safe_default(tmp_path, settings):
    builtins = tmp_path / "builtins"
    _write_theme(builtins, theme_id="known-good")
    settings.THEME_BUILTIN_DIR = builtins
    registry = ThemeRegistry()

    last_good = registry.select("damaged-active", "known-good")
    safe = registry.select("damaged-active", "also-missing")

    assert last_good.theme.id == "known-good"
    assert last_good.fallback_reason == "active-theme-unavailable"
    assert safe.theme.id == "safe-default"
    assert safe.fallback_reason == "safe-default-fallback"


def test_context_processor_falls_back_when_database_is_unavailable():
    with patch.object(SystemPreference.objects, "only", side_effect=DatabaseError("offline")):
        context = theme_context(None)

    assert context["active_theme"]["id"] == "safe-default"
    assert context["active_theme"]["resolved_appearance"] == "light"


@pytest.mark.django_db
def test_restored_missing_theme_preference_is_preserved_but_rendering_falls_back(tmp_path):
    preference = SystemPreference.objects.get()
    preference.active_theme_id = "theme-from-another-server"
    preference.last_known_good_theme_id = "missing-last-good"
    preference.save()
    file_bytes, _ = backup.build_encrypted_backup(BACKUP_PASSPHRASE)

    preference.active_theme_id = "safe-default"
    preference.last_known_good_theme_id = "safe-default"
    preference.save()
    with override_settings(BUSINESS_BACKUP_DIR=tmp_path):
        backup.restore_business_backup(
            file_bytes, BACKUP_PASSPHRASE, uploaded_filename="theme-restore.pfbackup"
        )

    restored = SystemPreference.objects.get()
    context = theme_context(None)
    assert restored.active_theme_id == "theme-from-another-server"
    assert context["active_theme"]["id"] == "safe-default"
    assert context["active_theme"]["requested_id"] == "theme-from-another-server"
    assert context["active_theme"]["fallback_reason"] == "safe-default-fallback"
