from django.db import models


class ImportBatch(models.Model):
    class Source(models.TextChoices):
        UNKNOWN = "UNKNOWN", "待识别"
        ALIPAY = "ALIPAY", "支付宝"
        WECHAT = "WECHAT", "微信"

    class Status(models.TextChoices):
        UPLOADED = "UPLOADED", "已上传"
        PARSING = "PARSING", "解析中"
        WAITING_CONFIRMATION = "WAITING_CONFIRMATION", "待确认"
        PARTIALLY_IMPORTED = "PARTIALLY_IMPORTED", "部分导入"
        COMPLETED = "COMPLETED", "已完成"
        FAILED = "FAILED", "失败"
        CANCELLED = "CANCELLED", "已取消"

    source = models.CharField(max_length=10, choices=Source.choices, default=Source.UNKNOWN)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.UPLOADED)
    original_filename = models.CharField(max_length=255)
    file_sha256 = models.CharField(max_length=64, db_index=True)
    temporary_file_path = models.CharField(max_length=1000, blank=True)
    parser_name = models.CharField(max_length=100, blank=True)
    parser_version = models.CharField(max_length=30, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True, db_index=True)
    parsed_at = models.DateTimeField(null=True, blank=True)
    file_deleted_at = models.DateTimeField(null=True, blank=True)
    total_count = models.PositiveIntegerField(default=0)
    imported_count = models.PositiveIntegerField(default=0)
    ignored_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    error_summary = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-uploaded_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["file_sha256"],
                condition=models.Q(
                    status__in=[
                        "WAITING_CONFIRMATION",
                        "PARTIALLY_IMPORTED",
                        "COMPLETED",
                    ]
                ),
                name="imports_unique_successful_file_hash",
            )
        ]

    def __str__(self) -> str:
        return f"{self.original_filename}（{self.get_status_display()}）"


class ImportRecord(models.Model):
    class CandidateTransactionType(models.TextChoices):
        INCOME = "INCOME", "收入"
        EXPENSE = "EXPENSE", "支出"
        REFUND = "REFUND", "退款"
        TRANSFER = "TRANSFER", "转账"
        UNKNOWN = "UNKNOWN", "未知"
        IGNORE = "IGNORE", "忽略"

    class Status(models.TextChoices):
        PENDING = "PENDING", "待确认"
        DUPLICATE_SUSPECTED = "DUPLICATE_SUSPECTED", "疑似重复"
        READY = "READY", "可导入"
        IMPORTED = "IMPORTED", "已导入"
        IGNORED = "IGNORED", "已忽略"
        FAILED = "FAILED", "解析失败"

    batch = models.ForeignKey(ImportBatch, on_delete=models.PROTECT, related_name="records")
    row_number = models.PositiveIntegerField()
    external_transaction_id = models.CharField(max_length=200, blank=True)
    external_order_id = models.CharField(max_length=200, blank=True)
    source_external_key = models.CharField(max_length=320, blank=True)
    exact_fingerprint = models.CharField(max_length=64, blank=True, db_index=True)
    occurred_at = models.DateTimeField(null=True, blank=True)
    candidate_transaction_type = models.CharField(
        max_length=10,
        choices=CandidateTransactionType.choices,
        default=CandidateTransactionType.UNKNOWN,
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    counterparty_raw = models.CharField(max_length=200, blank=True)
    payment_method_raw = models.CharField(max_length=200, blank=True)
    mapped_account = models.ForeignKey(
        "accounts.Account",
        on_delete=models.PROTECT,
        related_name="mapped_import_records",
        null=True,
        blank=True,
    )
    suggested_category = models.ForeignKey(
        "ledger.Category",
        on_delete=models.PROTECT,
        related_name="suggested_import_records",
        null=True,
        blank=True,
    )
    selected_category = models.ForeignKey(
        "ledger.Category",
        on_delete=models.PROTECT,
        related_name="selected_import_records",
        null=True,
        blank=True,
    )
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING)
    review_flags = models.JSONField(default=list, blank=True)
    imported_transaction = models.OneToOneField(
        "ledger.Transaction",
        on_delete=models.PROTECT,
        related_name="import_record",
        null=True,
        blank=True,
    )
    sanitized_raw_data = models.JSONField(default=dict, blank=True)
    error_message = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["row_number", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "row_number"], name="imports_unique_batch_row"
            ),
            models.UniqueConstraint(
                fields=["source_external_key"],
                condition=models.Q(status="IMPORTED") & ~models.Q(source_external_key=""),
                name="imports_unique_imported_external_key",
            ),
        ]
        indexes = [
            models.Index(fields=["batch", "status"], name="imports_batch_status_idx"),
            models.Index(fields=["external_transaction_id"], name="imports_external_id_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.batch_id}:{self.row_number} {self.get_status_display()}"
