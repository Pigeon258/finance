from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse

from apps.core.models import SystemPreference
from apps.core.themes import get_theme_registry


def test_aurora_ledger_is_complete_original_builtin_package():
    theme = get_theme_registry().get("aurora-ledger")

    assert theme is not None
    assert theme.source == "builtin"
    assert theme.capabilities == frozenset({"background", "charts", "safe-css", "tokens"})
    assert theme.appearance == "dark"
    assert theme.config["art"] == {
        "asset": "assets/background.webp",
        "focus": {"x": 0.82, "y": 0.24},
        "mode": "full",
        "overlay": "linear-gradient(90deg, rgb(5 9 25 / 42%), rgb(6 12 31 / 12%))",
        "safe_area": "left",
    }
    assert theme.chart_theme["color"][:3] == ["#65e6cf", "#7fcfff", "#9b82ff"]

    root = Path(settings.THEME_BUILTIN_DIR) / "aurora-ledger"
    assert (root / "assets" / "background.webp").stat().st_size < 100 * 1024
    assert (root / "preview.webp").stat().st_size < 50 * 1024
    sources = (root / "ASSET-SOURCES.txt").read_text(encoding="utf-8")
    license_text = (root / "LICENSE.txt").read_text(encoding="utf-8")
    assert "Third-party input images: none" in sources
    assert "Permission is granted" in license_text


@pytest.mark.django_db
def test_aurora_ledger_is_default_while_safe_default_remains_last_known_good():
    preference = SystemPreference.objects.get()

    assert preference.active_theme_id == "aurora-ledger"
    assert preference.last_known_good_theme_id == "safe-default"


@pytest.mark.django_db
def test_appearance_background_and_motion_preferences_reach_the_shared_shell(
    client, django_user_model
):
    owner = django_user_model.objects.create_superuser(
        username="theme-owner", password="correct horse battery staple"
    )
    SystemPreference.objects.update(
        appearance_mode=SystemPreference.AppearanceMode.LIGHT,
        reduce_motion=True,
        show_theme_background=False,
    )
    client.force_login(owner)

    response = client.get(reverse("core:home"))
    content = response.content.decode()

    assert 'data-bs-theme="light"' in content
    assert 'data-appearance-mode="light"' in content
    assert 'data-reduce-motion="true"' in content
    assert 'data-theme-background="false"' in content
    assert 'data-theme-id="aurora-ledger"' in content
