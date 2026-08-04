import re
from decimal import Decimal

import pytest
from django.conf import settings
from django.urls import reverse

from apps.accounts.models import Account
from apps.core.models import SystemPreference

PASSWORD = "correct horse battery staple"
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


@pytest.fixture
def owner(django_user_model):
    # 质量验收只创建唯一所有者，不写入账本事实。
    return django_user_model.objects.create_superuser(username="owner", password=PASSWORD)


def _relative_luminance(color: str) -> float:
    assert HEX_COLOR_RE.fullmatch(color)
    channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(foreground: str, background: str) -> float:
    light, dark = sorted(
        (_relative_luminance(foreground), _relative_luminance(background)), reverse=True
    )
    return (light + 0.05) / (dark + 0.05)


@pytest.mark.parametrize(
    ("foreground", "background"),
    [
        ("#1d2939", "#ffffff"),
        ("#667085", "#ffffff"),
        ("#ffffff", "#3157d5"),
        ("#dce9fb", "#080d21"),
        ("#a9b9d6", "#0e1834"),
        ("#071526", "#65e6cf"),
        ("#21334d", "#fafdff"),
        ("#506985", "#fafdff"),
    ],
)
def test_builtin_theme_text_pairs_meet_wcag_aa(foreground, background):
    assert _contrast_ratio(foreground, background) >= 4.5


def test_every_data_table_has_a_named_keyboard_scroll_region():
    templates = list((settings.BASE_DIR / "apps").rglob("*.html"))
    table_count = 0
    wrapper_count = 0

    for path in templates:
        content = path.read_text(encoding="utf-8")
        table_count += len(re.findall(r"<table(?:\s|>)", content))
        wrapper_count += len(
            re.findall(
                r'class="table-scroll"[^>]*tabindex="0"[^>]*role="region"'
                r'[^>]*aria-label="[^"]+"',
                content,
            )
        )

    assert table_count > 0
    assert wrapper_count == table_count


def test_motion_and_forced_color_fallbacks_are_global():
    app_css = (settings.BASE_DIR / "static" / "css" / "app.css").read_text(encoding="utf-8")
    aurora_css = (
        settings.BASE_DIR / "static" / "themes" / "aurora-ledger" / "theme.css"
    ).read_text(encoding="utf-8")

    assert "@media (prefers-reduced-motion: reduce)" in app_css
    assert '[data-reduce-motion="true"]' in app_css
    assert "animation-duration: 0.01ms !important" in app_css
    assert "transition-duration: 0.01ms !important" in app_css
    assert "@media (forced-colors: active)" in app_css
    assert '--pf-status-animation: none' in aurora_css


def test_visual_assets_stay_within_cold_load_budget():
    theme_root = settings.BASE_DIR / "static" / "themes"
    theme_files = [path for path in theme_root.rglob("*") if path.is_file()]
    aurora_root = theme_root / "aurora-ledger"

    assert sum(path.stat().st_size for path in theme_files) < 80 * 1024
    assert (aurora_root / "assets" / "background.webp").stat().st_size < 48 * 1024
    assert (aurora_root / "preview.webp").stat().st_size < 16 * 1024
    assert (settings.BASE_DIR / "static" / "css" / "app.css").stat().st_size < 32 * 1024


@pytest.mark.django_db
def test_form_errors_and_page_status_are_announced(client):
    response = client.post(reverse("core:login"), {"username": "", "password": ""})
    body = response.content.decode()

    assert response.status_code == 200
    assert 'aria-invalid="true"' in body
    assert 'aria-describedby="id_username_error"' in body
    assert 'class="skip-link"' in body
    assert 'id="main-content"' in body
    assert "role=\"{% if" not in body


@pytest.mark.django_db
def test_theme_switch_preserves_financial_values_and_form_contract(client, owner):
    account = Account.objects.get(account_type=Account.AccountType.BANK)
    account.initial_balance = Decimal("123.45")
    account.save(update_fields=["initial_balance", "updated_at"])
    client.force_login(owner)

    snapshots = []
    for theme_id in ("safe-default", "aurora-ledger"):
        SystemPreference.objects.update(
            active_theme_id=theme_id,
            last_known_good_theme_id=theme_id,
        )
        account_page = client.get(reverse("accounts:index")).content.decode()
        settings_page = client.get(reverse("core:settings")).content.decode()
        field_names = tuple(
            sorted(
                set(
                    re.findall(
                        r'<(?:input|select|textarea)[^>]+name="([^"]+)"', settings_page
                    )
                )
                - {"csrfmiddlewaretoken"}
            )
        )
        form_actions = tuple(sorted(re.findall(r'<form[^>]*action="([^"]*)"', settings_page)))
        snapshots.append(("123.45" in account_page, field_names, form_actions))

    assert snapshots[0] == snapshots[1]
    assert snapshots[0][0] is True


def test_visual_templates_keep_one_semantic_page_heading():
    page_templates = [
        path
        for root in (settings.BASE_DIR / "apps", settings.BASE_DIR / "templates" / "registration")
        for path in root.rglob("*.html")
    ]

    for path in page_templates:
        content = path.read_text(encoding="utf-8")
        assert len(re.findall(r"<h1(?:\s|>)", content)) == 1, path
