import hashlib
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse

PASSWORD = "correct horse battery staple"
BOOTSTRAP_HASHES = {
    "bootstrap.min.css": "d85327d99c7a3ee1f9b5d0500d1370acea3ad2db39c163c2f51f232baedbdede",
    "bootstrap.bundle.min.js": "e4fd49181388c48ec5040bd3fe66f57c29c8e67fcd8502b3354b96ec7ab47cc7",
}


@pytest.fixture
def owner(django_user_model):
    # 视觉测试只需要唯一系统所有者，不创建任何财务事实。
    return django_user_model.objects.create_superuser(username="owner", password=PASSWORD)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_bootstrap_assets_are_local_and_pinned():
    vendor = settings.BASE_DIR / "static" / "vendor" / "bootstrap"

    for filename, expected_hash in BOOTSTRAP_HASHES.items():
        assert _sha256(vendor / filename) == expected_hash
    assert (vendor / "LICENSE").is_file()


def test_application_templates_extend_the_shared_shell():
    template_roots = [settings.BASE_DIR / "templates", settings.BASE_DIR / "apps"]
    templates = []
    for root in template_roots:
        templates.extend(root.rglob("*.html"))

    excluded = {settings.BASE_DIR / "templates" / "base.html"}
    component_root = settings.BASE_DIR / "templates" / "components"
    page_templates = [
        path for path in templates if path not in excluded and component_root not in path.parents
    ]

    assert page_templates
    for path in page_templates:
        content = path.read_text(encoding="utf-8").lstrip("\ufeff\r\n ")
        assert content.startswith('{% extends "base.html" %}'), path


@pytest.mark.django_db
def test_authenticated_shell_exposes_stable_theme_parts(client, owner):
    client.force_login(owner)

    response = client.get(reverse("core:home"))
    body = response.content.decode()

    assert response.status_code == 200
    assert 'data-pf-part="app-shell"' in body
    assert 'data-pf-part="top-navigation"' in body
    assert 'data-pf-part="navigation-menu"' in body
    assert 'data-pf-part="content-panel"' in body
    assert "/static/vendor/bootstrap/bootstrap.min.css" in body
    assert "/static/vendor/bootstrap/bootstrap.bundle.min.js" in body
    assert "/static/themes/aurora-ledger/theme.css" in body
    assert reverse("ledger:transaction-index") in body
    assert reverse("core:settings") in body
    assert '<details class="mobile-navigation" hidden>' in body


def test_mobile_navigation_is_fail_closed_until_responsive_css_applies():
    app_css = (settings.BASE_DIR / "static" / "css" / "app.css").read_text(
        encoding="utf-8"
    )

    # 基础样式加载失败时依靠 hidden 避免桌面、移动导航同时暴露。
    assert ".mobile-navigation[hidden]" in app_css
    assert "@media (max-width: 52rem)" in app_css


@pytest.mark.django_db
def test_login_uses_auth_component_without_script_dependency(client):
    response = client.get(reverse("core:login"))
    body = response.content.decode()

    assert response.status_code == 200
    assert 'data-pf-part="auth-panel"' in body
    assert 'data-pf-part="form-panel"' in body
    assert 'class="skip-link"' in body
    assert "onclick=" not in body
    assert "安全登录" in body
    assert "/static/themes/aurora-ledger/theme.css" in body


def test_safe_default_defines_required_fallback_tokens():
    theme_css = (
        settings.BASE_DIR / "static" / "themes" / "safe-default" / "theme.css"
    ).read_text(encoding="utf-8")

    required_tokens = {
        "--pf-canvas",
        "--pf-surface",
        "--pf-text",
        "--pf-accent",
        "--pf-border",
        "--pf-focus",
        "--pf-success",
        "--pf-warning",
        "--pf-danger",
    }
    assert required_tokens <= {line.split(":", 1)[0].strip() for line in theme_css.splitlines()}
