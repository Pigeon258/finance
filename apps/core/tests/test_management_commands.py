from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

PASSWORD = "command-only owner password 2026"


@pytest.mark.django_db
def test_create_owner_creates_exactly_one_superuser(django_user_model):
    stdout = StringIO()
    with patch(
        "apps.core.management.commands.create_owner.getpass",
        side_effect=[PASSWORD, PASSWORD],
    ):
        call_command("create_owner", username="owner", stdout=stdout)

    owner = django_user_model.objects.get()
    assert owner.username == "owner"
    assert owner.is_superuser is True
    assert owner.check_password(PASSWORD)
    assert PASSWORD not in stdout.getvalue()

    with pytest.raises(CommandError, match="already exists"):
        call_command("create_owner", username="second-owner")


@pytest.mark.django_db
def test_create_owner_rejects_password_mismatch(django_user_model):
    with (
        patch(
            "apps.core.management.commands.create_owner.getpass",
            side_effect=[PASSWORD, "different password"],
        ),
        pytest.raises(CommandError, match="do not match"),
    ):
        call_command("create_owner")

    assert django_user_model.objects.count() == 0
