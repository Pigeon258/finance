from .platform import ColumnBillParser


class WeChatBillParser(ColumnBillParser):
    source = "WECHAT"
    parser_name = "wechat-pay-bill"
    parser_version = "1.0"
    platform_markers = ("微信支付账单", "微信支付")
    columns = {
        "external_transaction_id": ("交易单号", "微信支付单号"),
        "external_order_id": ("商户单号", "商家单号"),
        "occurred_at_raw": ("交易时间",),
        "direction_raw": ("收/支", "收支"),
        "amount_raw": ("金额(元)", "金额（元）", "金额"),
        "status_raw": ("当前状态", "交易状态", "状态"),
        "business_type_raw": ("交易类型", "类型"),
        "counterparty_raw": ("交易对方", "对方"),
        "item_name_raw": ("商品", "商品名称"),
        "payment_method_raw": ("支付方式", "收/付款方式"),
        "note_raw": ("备注",),
        "related_external_id": ("关联交易单号", "原交易单号"),
    }
