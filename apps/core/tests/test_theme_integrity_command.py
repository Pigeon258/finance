from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.core.models import SystemPreference


@pytest.mark.django_db
def test_theme_integrity_command_passes_for_builtin_default():
    output = StringIO()

    call_command("check_theme_integrity", "--strict", stdout=output)

    assert "主题完整性检查通过" in output.getvalue()
    assert "schema=1 contract=1" in output.getvalue()


@pytest.mark.django_db
def test_theme_integrity_command_strictly_rejects_fallback():
    SystemPreference.objects.update(
        active_theme_id="missing-theme",
        last_known_good_theme_id="safe-default",
    )

    with pytest.raises(CommandError, match="已回退为 safe-default"):
        call_command("check_theme_integrity", "--strict")


@pytest.mark.django_db
def test_theme_integrity_command_non_strict_mode_reports_safe_fallback():
    SystemPreference.objects.update(
        active_theme_id="missing-theme",
        last_known_good_theme_id="safe-default",
    )
    output = StringIO()

    call_command("check_theme_integrity", stdout=output)

    assert "已回退为 safe-default" in output.getvalue()
    assert "resolved=safe-default" in output.getvalue()
