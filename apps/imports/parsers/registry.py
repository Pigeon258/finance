from pathlib import Path

from .alipay import AlipayBillParser
from .base import BillParseError, BillParser
from .wechat import WeChatBillParser

PARSER_REGISTRY: tuple[BillParser, ...] = (AlipayBillParser(), WeChatBillParser())


def detect_parser(file_path: Path) -> BillParser:
    results = sorted(
        ((parser.detect(file_path).confidence, parser) for parser in PARSER_REGISTRY),
        key=lambda item: item[0],
        reverse=True,
    )
    if not results or results[0][0] < 80:
        raise BillParseError("无法识别账单来源或账单缺少必要列。")
    if len(results) > 1 and results[0][0] == results[1][0]:
        raise BillParseError("账单来源识别结果不唯一。")
    return results[0][1]
