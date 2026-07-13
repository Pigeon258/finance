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
    class DuplicateResolution(models.TextChoices):
        KEEP_MANUAL = "KEEP_MANUAL", "保留现有记录"
        REPLACE_MANUAL = "REPLACE_MANUAL", "使用导入记录替换"
        KEEP_BOTH = "KEEP_BOTH", "两条都保留"
        MERGE = "MERGE", "合并信息"

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
    duplicate_resolution = models.CharField(
        max_length=20, choices=DuplicateResolution.choices, blank=True
    )
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


class ImportDuplicateCandidate(models.Model):
    class MatchKind(models.TextChoices):
        EXACT_EXTERNAL_ID = "EXACT_EXTERNAL_ID", "外部流水号一致"
        EXACT_FINGERPRINT = "EXACT_FINGERPRINT", "标准指纹一致"
        FUZZY = "FUZZY", "疑似手工重复"
        REFUND_CANDIDATE = "REFUND_CANDIDATE", "退款原交易候选"

    import_record = models.ForeignKey(
        ImportRecord, on_delete=models.CASCADE, related_name="duplicate_candidates"
    )
    transaction = models.ForeignKey(
        "ledger.Transaction", on_delete=models.PROTECT, related_name="import_candidates"
    )
    match_kind = models.CharField(max_length=24, choices=MatchKind.choices)
    score = models.PositiveSmallIntegerField(default=0)
    reasons = models.JSONField(default=list, blank=True)
    is_selected = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-score", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["import_record", "transaction", "match_kind"],
                name="imports_unique_duplicate_candidate",
            ),
            models.UniqueConstraint(
                fields=["import_record"],
                condition=models.Q(is_selected=True),
                name="imports_one_selected_candidate",
            ),
            models.CheckConstraint(
                condition=models.Q(score__lte=100), name="imports_candidate_score_lte_100"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.import_record_id}: {self.get_match_kind_display()} {self.score}"


class MerchantCategoryRule(models.Model):
    class MatchTarget(models.TextChoices):
        MERCHANT = "MERCHANT", "交易对方"
        ITEM = "ITEM", "商品名称"
        BUSINESS_TYPE = "BUSINESS_TYPE", "平台业务类型"

    class MatchKind(models.TextChoices):
        EXACT = "EXACT", "精确匹配"
        CONTAINS = "CONTAINS", "包含匹配"

    name = models.CharField(max_length=100)
    match_target = models.CharField(max_length=20, choices=MatchTarget.choices)
    match_kind = models.CharField(max_length=10, choices=MatchKind.choices)
    pattern = models.CharField(max_length=200)
    category = models.ForeignKey(
        "ledger.Category", on_delete=models.PROTECT, related_name="merchant_category_rules"
    )
    priority = models.PositiveIntegerField(default=100)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["priority", "id"]
        indexes = [models.Index(fields=["is_active", "priority"])]
        constraints = [
            models.UniqueConstraint(
                fields=["match_target", "match_kind", "pattern"],
                name="imports_unique_category_rule_pattern",
            )
        ]

    def __str__(self) -> str:
        return self.name


class ImportAccountRule(models.Model):
    class MatchKind(models.TextChoices):
        EXACT = "EXACT", "精确匹配"
        CONTAINS = "CONTAINS", "包含匹配"

    name = models.CharField(max_length=100)
    source = models.CharField(
        max_length=10, choices=ImportBatch.Source.choices, default=ImportBatch.Source.UNKNOWN
    )
    match_kind = models.CharField(max_length=10, choices=MatchKind.choices)
    pattern = models.CharField(max_length=200)
    account = models.ForeignKey(
        "accounts.Account", on_delete=models.PROTECT, related_name="import_account_rules"
    )
    priority = models.PositiveIntegerField(default=100)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["priority", "id"]
        indexes = [models.Index(fields=["is_active", "priority"])]
        constraints = [
            models.UniqueConstraint(
                fields=["source", "match_kind", "pattern"],
                name="imports_unique_account_rule_pattern",
            )
        ]

    def __str__(self) -> str:
        return self.name
