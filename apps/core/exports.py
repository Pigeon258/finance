import csv
from datetime import date

from django.http import StreamingHttpResponse
from django.utils import timezone

from apps.analytics import selectors as analytics_selectors
from apps.ledger import selectors as ledger_selectors


class _CsvBuffer:
    def write(self, value):
        return value


def _money(value) -> str:
    return format(value, ".2f")


def _safe_text(value) -> str:
    text = "" if value is None else str(value)
    stripped = text.lstrip()
    if text and (text[0] in "=+-@\t\r" or (stripped and stripped[0] in "=+-@")):
        return "'" + text
    return text


def _csv_response(rows, *, filename: str) -> StreamingHttpResponse:
    writer = csv.writer(_CsvBuffer(), lineterminator="\r\n")

    def content():
        yield "\ufeff"
        for row in rows:
            yield writer.writerow(row)

    response = StreamingHttpResponse(content(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


def transaction_csv(*, date_from: date, date_to: date) -> StreamingHttpResponse:
    def rows():
        yield [
            "交易ID",
            "发生时间",
            "类型",
            "状态",
            "金额",
            "分类",
            "渠道",
            "交易对象",
            "备注",
            "来源",
            "账户及余额变化",
            "标签",
        ]
        transactions = ledger_selectors.transaction_list(
            filters={"date_from": date_from, "date_to": date_to}
        )
        for ledger_transaction in transactions.iterator(chunk_size=500):
            occurred_at = ledger_transaction.occurred_at
            if timezone.is_aware(occurred_at):
                occurred_at = timezone.localtime(occurred_at)
            entries = "; ".join(
                f"{_safe_text(entry.account.name)}: {_money(entry.balance_delta)}"
                for entry in ledger_transaction.entries.all()
            )
            yield [
                ledger_transaction.pk,
                occurred_at.isoformat(),
                _safe_text(ledger_transaction.get_transaction_type_display()),
                _safe_text(ledger_transaction.get_status_display()),
                _money(ledger_transaction.amount),
                _safe_text(ledger_transaction.category.name if ledger_transaction.category else ""),
                _safe_text(ledger_transaction.get_channel_display()),
                _safe_text(ledger_transaction.counterparty),
                _safe_text(ledger_transaction.note),
                _safe_text(ledger_transaction.get_source_display()),
                entries,
                _safe_text("; ".join(tag.name for tag in ledger_transaction.tags.all())),
            ]

    return _csv_response(rows(), filename=f"transactions-{date_from:%Y%m%d}-{date_to:%Y%m%d}.csv")


def monthly_statistics_csv(*, date_from: date, date_to: date) -> StreamingHttpResponse:
    report = analytics_selectors.report_snapshot(date_from=date_from, date_to=date_to)

    def rows():
        yield ["月份", "收入", "净支出"]
        for item in report.monthly:
            yield [item.period.strftime("%Y-%m"), _money(item.income), _money(item.expense)]

    return _csv_response(
        rows(), filename=f"monthly-statistics-{date_from:%Y%m%d}-{date_to:%Y%m%d}.csv"
    )
