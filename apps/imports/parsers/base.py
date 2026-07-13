from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol


class BillParseError(Exception):
    """A safe, user-facing parser error that never contains an original row."""


class MissingColumnsError(BillParseError):
    pass


class InvalidRecordError(BillParseError):
    pass


@dataclass(frozen=True)
class DetectionResult:
    source: str
    confidence: int


@dataclass(frozen=True)
class ParsedBillRecord:
    row_number: int
    external_transaction_id: str = ""
    external_order_id: str = ""
    occurred_at_raw: str = ""
    direction_raw: str = ""
    amount_raw: str = ""
    status_raw: str = ""
    business_type_raw: str = ""
    counterparty_raw: str = ""
    item_name_raw: str = ""
    payment_method_raw: str = ""
    note_raw: str = ""
    related_external_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedBillRecord:
    row_number: int
    source: str
    external_transaction_id: str
    external_order_id: str
    occurred_at: datetime
    amount: Decimal
    canonical_status: str
    candidate_transaction_type: str
    channel: str
    normalized_counterparty: str
    display_counterparty: str
    normalized_payment_method: str
    related_external_id: str
    exact_fingerprint: str
    review_flags: tuple[str, ...]
    sanitized_raw_data: dict[str, str]


class BillParser(Protocol):
    source: str
    parser_name: str
    parser_version: str

    def detect(self, file_path: Path) -> DetectionResult: ...

    def parse(self, file_path: Path) -> Iterable[ParsedBillRecord]: ...
