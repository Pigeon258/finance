from collections.abc import Iterable
from pathlib import Path

from .base import DetectionResult, MissingColumnsError, ParsedBillRecord
from .tabular import iter_tabular_rows, normalized_cell


class ColumnBillParser:
    source = ""
    parser_name = ""
    parser_version = "1.0"
    platform_markers: tuple[str, ...] = ()
    columns: dict[str, tuple[str, ...]] = {}
    required_fields: tuple[str, ...] = (
        "occurred_at_raw",
        "direction_raw",
        "amount_raw",
        "status_raw",
    )

    def _column_map(self, row: list[str]) -> dict[str, int]:
        normalized = [normalized_cell(value) for value in row]
        result = {}
        for field, aliases in self.columns.items():
            for alias in aliases:
                if alias in normalized:
                    result[field] = normalized.index(alias)
                    break
        return result

    def _find_header(self, file_path: Path):
        marker_found = False
        best: tuple[int, list[str], dict[str, int]] | None = None
        for row_number, row in enumerate(iter_tabular_rows(file_path), start=1):
            joined = " ".join(normalized_cell(value) for value in row)
            marker_found = marker_found or any(marker in joined for marker in self.platform_markers)
            mapping = self._column_map(row)
            if best is None or len(mapping) > len(best[2]):
                best = (row_number, row, mapping)
            if all(field in mapping for field in self.required_fields):
                return row_number, mapping, marker_found
            if row_number >= 50:
                break
        return (best[0], best[2], marker_found) if best else (0, {}, marker_found)

    def detect(self, file_path: Path) -> DetectionResult:
        _, mapping, marker_found = self._find_header(file_path)
        matched_required = sum(field in mapping for field in self.required_fields)
        confidence = matched_required * 20 + (20 if marker_found else 0)
        return DetectionResult(self.source, min(confidence, 100))

    def parse(self, file_path: Path) -> Iterable[ParsedBillRecord]:
        header_number, mapping, _ = self._find_header(file_path)
        missing = [field for field in self.required_fields if field not in mapping]
        if missing:
            raise MissingColumnsError("账单缺少必要列，无法解析。")
        for row_number, row in enumerate(iter_tabular_rows(file_path), start=1):
            if row_number <= header_number or not any(normalized_cell(value) for value in row):
                continue

            def value(field: str, current_row=row) -> str:
                index = mapping.get(field)
                return (
                    normalized_cell(current_row[index])
                    if index is not None and index < len(current_row)
                    else ""
                )

            yield ParsedBillRecord(
                row_number=row_number,
                external_transaction_id=value("external_transaction_id"),
                external_order_id=value("external_order_id"),
                occurred_at_raw=value("occurred_at_raw"),
                direction_raw=value("direction_raw"),
                amount_raw=value("amount_raw"),
                status_raw=value("status_raw"),
                business_type_raw=value("business_type_raw"),
                counterparty_raw=value("counterparty_raw"),
                item_name_raw=value("item_name_raw"),
                payment_method_raw=value("payment_method_raw"),
                note_raw=value("note_raw"),
                related_external_id=value("related_external_id"),
            )
