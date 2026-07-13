from decimal import Decimal
from pathlib import Path

import pytest

from apps.imports.normalization import normalize_record
from apps.imports.parsers import detect_parser
from apps.imports.parsers.base import BillParseError, InvalidRecordError

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("relative_path", "source", "types", "amounts"),
    [
        (
            "alipay/v1/normal.csv",
            "ALIPAY",
            ["EXPENSE", "INCOME"],
            [Decimal("88.50"), Decimal("500.00")],
        ),
        (
            "wechat/v1/normal.csv",
            "WECHAT",
            ["EXPENSE", "REFUND"],
            [Decimal("25.00"), Decimal("10.00")],
        ),
    ],
)
def test_versioned_platform_fixtures_detect_parse_and_normalize(
    relative_path, source, types, amounts
):
    path = FIXTURES / relative_path
    parser = detect_parser(path)
    parsed = list(parser.parse(path))
    normalized = [normalize_record(source=parser.source, record=row) for row in parsed]

    assert parser.source == source
    assert [row.candidate_transaction_type for row in normalized] == types
    assert [row.amount for row in normalized] == amounts
    assert all(isinstance(row.amount, Decimal) for row in normalized)
    assert all(len(row.exact_fingerprint) == 64 for row in normalized)


def test_missing_required_column_is_rejected_without_row_content():
    path = FIXTURES / "alipay/v1/missing-column.csv"
    parser = detect_parser(path)
    with pytest.raises(BillParseError, match="缺少必要列") as captured:
        list(parser.parse(path))
    assert "202607010001" not in str(captured.value)


def test_invalid_amount_is_a_safe_record_error():
    path = FIXTURES / "alipay/v1/invalid-amount.csv"
    parser = detect_parser(path)
    parsed = next(iter(parser.parse(path)))
    with pytest.raises(InvalidRecordError, match="精确到分"):
        normalize_record(source=parser.source, record=parsed)


def test_unknown_status_is_retained_for_review():
    path = FIXTURES / "wechat/v1/unknown-status.csv"
    parser = detect_parser(path)
    normalized = normalize_record(source=parser.source, record=next(iter(parser.parse(path))))
    assert normalized.canonical_status == "UNKNOWN"
    assert "UNKNOWN_STATUS" in normalized.review_flags


def test_gb18030_csv_is_supported(tmp_path):
    content = """微信支付账单明细
交易时间,交易类型,交易对方,商品,收/支,金额(元),支付方式,当前状态,交易单号,商户单号,备注
2026-07-03 12:30:00,商户消费,示例餐厅,午餐,支出,25.00,零钱,支付成功,WX-1,M-1,测试
"""
    path = tmp_path / "wechat.csv"
    path.write_bytes(content.encode("gb18030"))
    parser = detect_parser(path)
    assert parser.source == "WECHAT"
    assert len(list(parser.parse(path))) == 1


def test_sensitive_account_numbers_are_redacted_before_persistence_shape():
    path = FIXTURES / "wechat/v1/normal.csv"
    parser = detect_parser(path)
    parsed = next(iter(parser.parse(path)))
    replaced = parsed.__class__(
        **{
            **parsed.__dict__,
            "payment_method_raw": "银行卡 6222021234567890",
            "note_raw": "付款账号 1234567890123456",
        }
    )
    normalized = normalize_record(source=parser.source, record=replaced)
    persisted_text = str(normalized.sanitized_raw_data)
    assert "6222021234567890" not in persisted_text
    assert "1234567890123456" not in persisted_text
    assert "****7890" in persisted_text
    assert "SENSITIVE_VALUE_REDACTED" in normalized.review_flags
