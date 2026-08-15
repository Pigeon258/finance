from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.accounts.models import Account
from apps.ledger import services
from apps.ledger.models import Category, Tag, Transaction

TZ = ZoneInfo("Asia/Shanghai")
PASSWORD = "correct horse battery staple"


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_superuser(username="owner", password=PASSWORD)


@pytest.fixture
def authenticated_client(client, owner):
    client.force_login(owner)
    return client


@pytest.fixture
def bank():
    return Account.objects.get(account_type=Account.AccountType.BANK)


@pytest.fixture
def expense_category():
    return Category.objects.get(name="餐饮")


def _create_tag(client, *, name, applies_to):
    return client.post(
        reverse("ledger:tag-create"),
        {"name": name, "applies_to": applies_to, "is_active": "on"},
    )


@pytest.mark.django_db
def test_tag_management_pages_require_login(client):
    assert client.get(reverse("ledger:tag-index")).status_code == 302
    assert client.get(reverse("ledger:tag-create")).status_code == 302


@pytest.mark.django_db
def test_tag_can_be_created_edited_toggled_and_deleted_when_unused(authenticated_client):
    assert authenticated_client.get(reverse("ledger:tag-index")).status_code == 200

    response = _create_tag(
        authenticated_client, name="工资收入", applies_to=Tag.AppliesTo.INCOME
    )
    assert response.status_code == 302
    tag = Tag.objects.get(name="工资收入")
    assert tag.applies_to == Tag.AppliesTo.INCOME
    assert tag.is_active is True

    response = authenticated_client.post(
        reverse("ledger:tag-edit", args=[tag.id]),
        {"name": "主动收入", "applies_to": Tag.AppliesTo.INCOME, "is_active": "on"},
    )
    assert response.status_code == 302
    tag.refresh_from_db()
    assert tag.name == "主动收入"

    response = authenticated_client.post(reverse("ledger:tag-toggle", args=[tag.id]))
    assert response.status_code == 302
    tag.refresh_from_db()
    assert tag.is_active is False

    response = authenticated_client.post(reverse("ledger:tag-delete", args=[tag.id]))
    assert response.status_code == 302
    assert not Tag.objects.filter(pk=tag.pk).exists()


@pytest.mark.django_db
def test_tag_names_are_unique_within_the_same_type(authenticated_client):
    _create_tag(authenticated_client, name="同名标签", applies_to=Tag.AppliesTo.EXPENSE)
    response = _create_tag(authenticated_client, name="同名标签", applies_to=Tag.AppliesTo.EXPENSE)

    assert response.status_code == 200
    assert Tag.objects.filter(name="同名标签").count() == 1

    _create_tag(authenticated_client, name="同名标签", applies_to=Tag.AppliesTo.INCOME)
    assert Tag.objects.filter(name="同名标签").count() == 2


@pytest.mark.django_db
def test_used_tag_cannot_be_deleted_or_change_type(
    authenticated_client, bank, expense_category
):
    tag = Tag.objects.get(name="冲动消费")
    services.create_expense(
        account=bank,
        category=expense_category,
        amount=Decimal("10.00"),
        occurred_at=datetime(2026, 7, 10, 12, 0, tzinfo=TZ),
        channel=Transaction.Channel.BANK,
        tags=[tag],
    )

    response = authenticated_client.post(reverse("ledger:tag-delete", args=[tag.id]))
    assert response.status_code == 302
    assert Tag.objects.filter(pk=tag.pk).exists()

    edit_page = authenticated_client.get(reverse("ledger:tag-edit", args=[tag.id]))
    assert 'name="applies_to" disabled' in edit_page.content.decode()

    with pytest.raises(ValidationError, match="不能修改收入/支出类型"):
        services.update_tag(
            tag=tag,
            name=tag.name,
            applies_to=Tag.AppliesTo.INCOME,
            is_active=True,
        )
