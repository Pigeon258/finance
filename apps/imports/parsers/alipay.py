from .platform import ColumnBillParser


class AlipayBillParser(ColumnBillParser):
    source = "ALIPAY"
    parser_name = "alipay-bill"
    parser_version = "1.0"
    platform_markers = ("支付宝交易记录", "支付宝")
    columns = {
        "external_transaction_id": ("交易号", "支付宝交易号"),
        "external_order_id": ("商家订单号", "商户订单号"),
        "occurred_at_raw": ("交易创建时间", "创建时间", "付款时间"),
        "direction_raw": ("收/支", "收支"),
        "amount_raw": ("金额（元）", "金额(元)", "金额"),
        "status_raw": ("交易状态", "状态"),
        "business_type_raw": ("类型", "交易分类"),
        "counterparty_raw": ("交易对方", "对方"),
        "item_name_raw": ("商品名称", "商品说明"),
        "payment_method_raw": ("收/付款方式", "付款方式", "资金状态"),
        "note_raw": ("备注",),
        "related_external_id": ("关联交易号", "原交易号"),
    }
