from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Account
from apps.imports import review
from apps.imports import services as import_services
from apps.imports.confirmation import confirm_records
from apps.imports.models import (
    ImportAccountRule,
    ImportBatch,
    ImportDuplicateCandidate,
    ImportRecord,
    MerchantCategoryRule,
)
from apps.ledger import services as ledger_services
from apps.ledger.models import Category, Transaction

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def expense_category():
    return Category.objects.get(name="餐饮", category_type=Category.CategoryType.EXPENSE)


@pytest.fixture
def income_category():
    return Category.objects.get(name="其他收入", category_type=Category.CategoryType.INCOME)


@pytest.fixture
def wechat_account():
    return Account.objects.get(account_type=Account.AccountType.WECHAT)


@pytest.fixture
def bank_account():
    return Account.objects.get(account_type=Account.AccountType.BANK)


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_superuser(username="owner", password="safe-password-1")


def _batch(*, suffix="a", source=ImportBatch.Source.WECHAT):
    return ImportBatch.objects.create(
        source=source,
        status=ImportBatch.Status.WAITING_CONFIRMATION,
        original_filename=f"{suffix}.csv",
        file_sha256=(suffix * 64)[:64],
        total_count=1,
    )


def _record(
    *,
    batch,
    row=1,
    transaction_type=ImportRecord.CandidateTransactionType.EXPENSE,
    account=None,
    category=None,
    status=ImportRecord.Status.READY,
    external_id="",
    amount="20.00",
    counterparty="示例餐厅",
    occurred_at=None,
):
    return ImportRecord.objects.create(
        batch=batch,
        row_number=row,
        source_external_key=f"{batch.source}:{external_id}" if external_id else "",
        external_transaction_id=external_id,
        exact_fingerprint=f"fingerprint-{batch.pk}-{row}",
        occurred_at=occurred_at or timezone.now(),
        candidate_transaction_type=transaction_type,
        amount=Decimal(amount),
        counterparty_raw=counterparty,
        payment_method_raw="微信零钱",
        mapped_account=account,
        selected_category=category,
        status=status,
        sanitized_raw_data={"note": "导入备注"},
    )


@pytest.mark.django_db
def test_rule_precedence_priority_and_inactive_rules(
    expense_category, wechat_account, bank_account
):
    other = Category.objects.get(name="其他", category_type=Category.CategoryType.EXPENSE)
    batch = _batch()
    record = _record(batch=batch, status=ImportRecord.Status.PENDING, counterparty="示例餐厅")
    MerchantCategoryRule.objects.create(
        name="高优先级包含",
        match_target=MerchantCategoryRule.MatchTarget.MERCHANT,
        match_kind=MerchantCategoryRule.MatchKind.CONTAINS,
        pattern="示例",
        category=other,
        priority=1,
    )
    MerchantCategoryRule.objects.create(
        name="精确优先",
        match_target=MerchantCategoryRule.MatchTarget.MERCHANT,
        match_kind=MerchantCategoryRule.MatchKind.EXACT,
        pattern="示例餐厅",
        category=expense_category,
        priority=999,
    )
    ImportAccountRule.objects.create(
        name="停用精确",
        source=ImportBatch.Source.WECHAT,
        match_kind=ImportAccountRule.MatchKind.EXACT,
        pattern="微信零钱",
        account=bank_account,
        priority=1,
        is_active=False,
    )
    review.prepare_record_for_review(record=record)
    record.refresh_from_db()
    assert record.selected_category == expense_category
    assert record.mapped_account == wechat_account
    assert record.status == ImportRecord.Status.READY


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("score", "level"), [(69, "NONE"), (70, "POSSIBLE"), (84, "POSSIBLE"), (85, "HIGH")]
)
def test_duplicate_score_boundaries(score, level):
    assert review.duplicate_level(score) == level


@pytest.mark.django_db
def test_fuzzy_manual_duplicate_is_only_marked(expense_category, wechat_account):
    occurred_at = timezone.now()
    manual = ledger_services.create_expense(
        account=wechat_account,
        category=expense_category,
        amount=Decimal("20.00"),
        occurred_at=occurred_at,
        channel=Transaction.Channel.OTHER,
        counterparty="示例餐厅",
    )
    record = _record(
        batch=_batch(),
        account=wechat_account,
        category=expense_category,
        status=ImportRecord.Status.PENDING,
        occurred_at=occurred_at + timedelta(minutes=5),
    )
    review.prepare_record_for_review(record=record)
    record.refresh_from_db()
    candidate = record.duplicate_candidates.get(match_kind=ImportDuplicateCandidate.MatchKind.FUZZY)
    assert candidate.transaction == manual
    assert candidate.score == 100
    assert record.status == ImportRecord.Status.DUPLICATE_SUSPECTED
    assert Transaction.objects.count() == 1


@pytest.mark.django_db
def test_exact_fingerprint_marks_cross_batch_candidate(expense_category, wechat_account):
    first = _record(batch=_batch(suffix="f"), account=wechat_account, category=expense_category)
    first.exact_fingerprint = "same-fingerprint"
    first.save(update_fields=["exact_fingerprint"])
    confirm_records(batch=first.batch, record_ids=[first.id])
    second = _record(
        batch=_batch(suffix="g"),
        account=wechat_account,
        category=expense_category,
        status=ImportRecord.Status.PENDING,
    )
    second.exact_fingerprint = "same-fingerprint"
    second.save(update_fields=["exact_fingerprint"])
    review.prepare_record_for_review(record=second)
    second.refresh_from_db()
    assert second.status == ImportRecord.Status.DUPLICATE_SUSPECTED
    assert second.duplicate_candidates.filter(
        match_kind=ImportDuplicateCandidate.MatchKind.EXACT_FINGERPRINT
    ).exists()


@pytest.mark.django_db
def test_unknown_source_and_payment_method_require_manual_account(expense_category):
    record = _record(
        batch=_batch(source=ImportBatch.Source.UNKNOWN),
        category=expense_category,
        status=ImportRecord.Status.PENDING,
    )
    record.payment_method_raw = "无法识别的账户"
    record.save(update_fields=["payment_method_raw"])
    review.prepare_record_for_review(record=record)
    record.refresh_from_db()
    assert record.mapped_account is None
    assert record.status == ImportRecord.Status.PENDING


@pytest.mark.django_db
def test_confirm_uses_ledger_service_and_double_submit_is_idempotent(
    expense_category, wechat_account
):
    batch = _batch()
    record = _record(
        batch=batch, account=wechat_account, category=expense_category, external_id="WX-001"
    )
    first = confirm_records(batch=batch, record_ids=[record.id])
    second = confirm_records(batch=batch, record_ids=[record.id])
    record.refresh_from_db()
    batch.refresh_from_db()
    assert first.imported_count == 1
    assert second.skipped_count == 1
    assert Transaction.objects.count() == 1
    assert record.imported_transaction.source == Transaction.Source.IMPORT
    assert record.imported_transaction.entries.get().balance_delta == Decimal("-20.00")
    assert batch.status == ImportBatch.Status.COMPLETED


@pytest.mark.django_db
def test_parsed_bill_can_be_reviewed_and_confirmed_end_to_end(
    settings, tmp_path, expense_category, income_category
):
    settings.IMPORT_TMP_DIR = tmp_path / "imports"
    MerchantCategoryRule.objects.create(
        name="书店",
        match_target=MerchantCategoryRule.MatchTarget.MERCHANT,
        match_kind=MerchantCategoryRule.MatchKind.EXACT,
        pattern="示例书店",
        category=expense_category,
        priority=10,
    )
    MerchantCategoryRule.objects.create(
        name="家人",
        match_target=MerchantCategoryRule.MatchTarget.MERCHANT,
        match_kind=MerchantCategoryRule.MatchKind.EXACT,
        pattern="家人",
        category=income_category,
        priority=10,
    )
    fixture = FIXTURES / "alipay/v1/normal.csv"
    result = import_services.process_uploaded_bill(
        SimpleUploadedFile(fixture.name, fixture.read_bytes(), content_type="text/csv")
    )
    records = list(result.batch.records.order_by("row_number"))
    assert all(record.status == ImportRecord.Status.READY for record in records)
    confirmed = confirm_records(batch=result.batch, record_ids=[record.id for record in records])
    assert confirmed.imported_count == 2
    assert Transaction.objects.filter(source=Transaction.Source.IMPORT).count() == 2


@pytest.mark.django_db
def test_any_failure_rolls_back_whole_confirmation(expense_category, wechat_account):
    batch = _batch()
    first = _record(batch=batch, row=1, account=wechat_account, category=expense_category)
    second = _record(batch=batch, row=2, account=wechat_account, category=None)
    with pytest.raises(ValidationError, match="缺少账户或分类"):
        confirm_records(batch=batch, record_ids=[first.id, second.id])
    first.refresh_from_db()
    assert first.status == ImportRecord.Status.READY
    assert first.imported_transaction_id is None
    assert Transaction.objects.count() == 0


@pytest.mark.django_db
def test_confirmation_limit_accepts_500_and_rejects_501():
    batch = _batch()
    records = [
        ImportRecord(batch=batch, row_number=index, status=ImportRecord.Status.IGNORED)
        for index in range(1, 502)
    ]
    ImportRecord.objects.bulk_create(records)
    ids = list(batch.records.order_by("id").values_list("id", flat=True))
    assert confirm_records(batch=batch, record_ids=ids[:500]).skipped_count == 500
    with pytest.raises(ValidationError, match="最多确认 500"):
        confirm_records(batch=batch, record_ids=ids)


@pytest.mark.django_db
def test_external_id_duplicate_is_blocked_across_batches(expense_category, wechat_account):
    first = _record(
        batch=_batch(suffix="a"),
        account=wechat_account,
        category=expense_category,
        external_id="SAME-ID",
    )
    confirm_records(batch=first.batch, record_ids=[first.id])
    second = _record(
        batch=_batch(suffix="b"),
        account=wechat_account,
        category=expense_category,
        external_id="SAME-ID",
    )
    with pytest.raises(ValidationError, match="外部流水号已经入账"):
        confirm_records(batch=second.batch, record_ids=[second.id])
    assert Transaction.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_database_constraint_protects_external_id(expense_category, wechat_account):
    first = _record(
        batch=_batch(suffix="c"),
        account=wechat_account,
        category=expense_category,
        external_id="DB-ID",
        status=ImportRecord.Status.IMPORTED,
    )
    second = _record(
        batch=_batch(suffix="d"),
        account=wechat_account,
        category=expense_category,
        external_id="DB-ID",
    )
    assert first.source_external_key == second.source_external_key
    with pytest.raises(IntegrityError), transaction.atomic():
        ImportRecord.objects.filter(pk=second.pk).update(status=ImportRecord.Status.IMPORTED)


def _select_candidate(record, manual):
    return ImportDuplicateCandidate.objects.create(
        import_record=record,
        transaction=manual,
        match_kind=ImportDuplicateCandidate.MatchKind.FUZZY,
        score=90,
        reasons=["测试"],
        is_selected=True,
    )


@pytest.mark.django_db
@pytest.mark.parametrize("resolution", ["KEEP_MANUAL", "KEEP_BOTH", "REPLACE_MANUAL", "MERGE"])
def test_all_duplicate_decisions(resolution, expense_category, wechat_account):
    manual = ledger_services.create_expense(
        account=wechat_account,
        category=expense_category,
        amount=Decimal("20.00"),
        occurred_at=timezone.now(),
        channel=Transaction.Channel.OTHER,
        counterparty="",
    )
    record = _record(
        batch=_batch(suffix=resolution[0].lower()),
        account=wechat_account,
        category=expense_category,
        status=ImportRecord.Status.DUPLICATE_SUSPECTED,
    )
    record.duplicate_resolution = resolution
    record.save(update_fields=["duplicate_resolution"])
    _select_candidate(record, manual)
    confirm_records(batch=record.batch, record_ids=[record.id])
    record.refresh_from_db()
    manual.refresh_from_db()
    if resolution == "KEEP_MANUAL":
        assert record.status == ImportRecord.Status.IGNORED
        assert Transaction.objects.count() == 1
    elif resolution == "KEEP_BOTH":
        assert record.status == ImportRecord.Status.IMPORTED
        assert Transaction.objects.count() == 2
    elif resolution == "REPLACE_MANUAL":
        assert manual.status == Transaction.Status.VOID
        assert Transaction.objects.count() == 2
    else:
        assert record.imported_transaction == manual
        assert manual.counterparty == "示例餐厅"
        assert manual.channel == Transaction.Channel.WECHAT
        assert Transaction.objects.count() == 1


@pytest.mark.django_db
def test_refund_candidate_can_be_selected_and_confirmed(expense_category, wechat_account):
    expense = ledger_services.create_expense(
        account=wechat_account,
        category=expense_category,
        amount=Decimal("50.00"),
        occurred_at=timezone.now() - timedelta(days=1),
        channel=Transaction.Channel.WECHAT,
    )
    record = _record(
        batch=_batch(),
        transaction_type=ImportRecord.CandidateTransactionType.REFUND,
        account=wechat_account,
        category=expense_category,
        status=ImportRecord.Status.PENDING,
        amount="20.00",
    )
    review.prepare_record_for_review(record=record)
    candidate = record.duplicate_candidates.get(
        match_kind=ImportDuplicateCandidate.MatchKind.REFUND_CANDIDATE,
        transaction=expense,
    )
    candidate.is_selected = True
    candidate.save(update_fields=["is_selected"])
    record.status = ImportRecord.Status.READY
    record.save(update_fields=["status"])
    confirm_records(batch=record.batch, record_ids=[record.id])
    record.refresh_from_db()
    assert record.imported_transaction.transaction_type == Transaction.TransactionType.REFUND
    assert record.imported_transaction.related_transaction == expense


@pytest.mark.django_db
def test_rule_pages_and_bulk_mapping(client, owner, expense_category, wechat_account):
    client.force_login(owner)
    response = client.post(
        reverse("imports:category-rule-create"),
        {
            "name": "餐厅",
            "match_target": "MERCHANT",
            "match_kind": "CONTAINS",
            "pattern": "  示例餐厅  ",
            "category": expense_category.id,
            "priority": 10,
            "is_active": "on",
        },
    )
    assert response.status_code == 302
    assert MerchantCategoryRule.objects.get().pattern == "示例餐厅"
    batch = _batch()
    record = _record(batch=batch, status=ImportRecord.Status.PENDING)
    response = client.post(
        reverse("imports:batch-action", args=[batch.id]),
        {"record_ids": [record.id], "action": "SET_ACCOUNT", "account": wechat_account.id},
    )
    assert response.status_code == 302
    record.refresh_from_db()
    assert record.mapped_account == wechat_account
    response = client.post(
        reverse("imports:record-review", args=[batch.id, record.id]),
        {
            "mapped_account": wechat_account.id,
            "selected_category": expense_category.id,
            "duplicate_resolution": "",
            "selected_candidate": "",
            "save_merchant_rule": "on",
        },
    )
    assert response.status_code == 302
    assert MerchantCategoryRule.objects.filter(
        match_kind=MerchantCategoryRule.MatchKind.EXACT,
        pattern="示例餐厅",
        category=expense_category,
    ).exists()
    assert client.get(reverse("imports:rules")).status_code == 200
