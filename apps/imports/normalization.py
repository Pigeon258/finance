import hashlib
import re
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from .parsers.base import InvalidRecordError, NormalizedBillRecord, ParsedBillRecord

MONEY_QUANTUM = Decimal("0.01")
MAX_MONEY = Decimal("999999999999.99")
LONG_DIGIT_PATTERN = re.compile(r"(?<!\d)(\d{8,})(?!\d)")
SPACE_PATTERN = re.compile(r"\s+")

STATUS_MAP = {
    "交易成功": "COMPLETED",
    "支付成功": "COMPLETED",
    "已收钱": "COMPLETED",
    "对方已收钱": "COMPLETED",
    "转账成功": "COMPLETED",
    "退款成功": "REFUNDED",
    "已退款": "REFUNDED",
    "已全额退款": "REFUNDED",
    "部分退款": "PARTIALLY_REFUNDED",
    "等待付款": "PENDING",
    "待支付": "PENDING",
    "交易关闭": "CLOSED",
    "已关闭": "CLOSED",
    "交易失败": "FAILED",
    "支付失败": "FAILED",
}


def _redact_digits(match: re.Match) -> str:
    digits = match.group(1)
    return f"****{digits[-4:]}"


def sanitize_text(value: str, *, max_length: int = 200) -> tuple[str, bool]:
    value = unicodedata.normalize("NFKC", str(value or ""))
    value = "".join(character for character in value if character.isprintable())
    value = SPACE_PATTERN.sub(" ", value).strip()
    redacted = LONG_DIGIT_PATTERN.sub(_redact_digits, value)
    return redacted[:max_length], redacted != value


def sanitize_identifier(value: str, *, max_length: int = 200) -> str:
    value = unicodedata.normalize("NFKC", str(value or ""))
    value = "".join(character for character in value if character.isprintable())
    return SPACE_PATTERN.sub(" ", value).strip()[:max_length]


def normalize_key(value: str) -> str:
    sanitized, _ = sanitize_text(value)
    return SPACE_PATTERN.sub(" ", sanitized.casefold()).strip()


def parse_money(raw: str) -> Decimal:
    cleaned = str(raw or "").strip().replace(",", "")
    cleaned = cleaned.removeprefix("¥").removeprefix("￥").strip()
    try:
        value = Decimal(cleaned)
    except (InvalidOperation, ValueError) as error:
        raise InvalidRecordError("金额格式无效。") from error
    if not value.is_finite() or value <= 0 or value > MAX_MONEY:
        raise InvalidRecordError("金额必须为范围内的正数。")
    if value != value.quantize(MONEY_QUANTUM):
        raise InvalidRecordError("金额最多只能精确到分。")
    return value


def parse_occurred_at(raw: str) -> datetime:
    value = str(raw or "").strip()
    parsed = None
    for format_string in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            parsed = datetime.strptime(value, format_string)
            break
        except ValueError:
            continue
    if parsed is None:
        raise InvalidRecordError("交易时间格式无效。")
    return timezone.make_aware(parsed, timezone.get_current_timezone())


def _canonical_status(raw: str) -> str:
    return STATUS_MAP.get(str(raw or "").strip(), "UNKNOWN")


def _candidate_type(record: ParsedBillRecord, canonical_status: str) -> str:
    combined = f"{record.business_type_raw} {record.status_raw}"
    if "退款" in combined:
        return "REFUND"
    if canonical_status in {"CLOSED", "FAILED"}:
        return "IGNORE"
    direction = str(record.direction_raw or "").strip()
    if direction in {"支出", "付款"}:
        return "EXPENSE"
    if direction in {"收入", "收款"}:
        return "INCOME"
    return "UNKNOWN"


def normalize_record(*, source: str, record: ParsedBillRecord) -> NormalizedBillRecord:
    amount = parse_money(record.amount_raw)
    occurred_at = parse_occurred_at(record.occurred_at_raw)
    canonical_status = _canonical_status(record.status_raw)
    candidate_type = _candidate_type(record, canonical_status)
    review_flags: list[str] = []
    if canonical_status == "UNKNOWN":
        review_flags.append("UNKNOWN_STATUS")
    if candidate_type == "UNKNOWN":
        review_flags.append("UNKNOWN_DIRECTION")
    if not record.external_transaction_id.strip():
        review_flags.append("MISSING_EXTERNAL_ID")

    external_id = sanitize_identifier(record.external_transaction_id)
    order_id = sanitize_identifier(record.external_order_id)
    counterparty, counterparty_redacted = sanitize_text(record.counterparty_raw)
    item_name, item_redacted = sanitize_text(record.item_name_raw)
    payment_method, payment_redacted = sanitize_text(record.payment_method_raw)
    note, note_redacted = sanitize_text(record.note_raw, max_length=500)
    related_id, related_redacted = sanitize_text(record.related_external_id)
    if any(
        [
            counterparty_redacted,
            item_redacted,
            payment_redacted,
            note_redacted,
            related_redacted,
        ]
    ):
        review_flags.append("SENSITIVE_VALUE_REDACTED")

    display_counterparty = counterparty or item_name or "未提供交易对方"
    normalized_counterparty = normalize_key(display_counterparty)
    normalized_payment_method = normalize_key(payment_method)
    fingerprint_input = "|".join(
        [
            source,
            candidate_type,
            format(amount, ".2f"),
            occurred_at.isoformat(),
            normalized_counterparty,
            normalized_payment_method,
            order_id,
        ]
    )
    fingerprint = hashlib.sha256(fingerprint_input.encode("utf-8")).hexdigest()
    channel = "ALIPAY" if source == "ALIPAY" else "WECHAT"
    return NormalizedBillRecord(
        row_number=record.row_number,
        source=source,
        external_transaction_id=external_id,
        external_order_id=order_id,
        occurred_at=occurred_at,
        amount=amount,
        canonical_status=canonical_status,
        candidate_transaction_type=candidate_type,
        channel=channel,
        normalized_counterparty=normalized_counterparty,
        display_counterparty=display_counterparty,
        normalized_payment_method=normalized_payment_method,
        related_external_id=related_id,
        exact_fingerprint=fingerprint,
        review_flags=tuple(review_flags),
        sanitized_raw_data={
            "canonical_status": canonical_status,
            "channel": channel,
            "direction": sanitize_text(record.direction_raw)[0],
            "business_type": sanitize_text(record.business_type_raw)[0],
            "status": sanitize_text(record.status_raw)[0],
            "item_name": item_name,
            "counterparty": counterparty,
            "payment_method": payment_method,
            "note": note,
            "related_external_id": related_id,
        },
    )
