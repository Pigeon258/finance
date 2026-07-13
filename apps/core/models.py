from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class SystemPreference(models.Model):
    SINGLETON_ID = 1

    id = models.PositiveSmallIntegerField(primary_key=True, default=SINGLETON_ID, editable=False)
    time_zone = models.CharField(max_length=64, default="Asia/Shanghai")
    category_warning_threshold = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("80.00"),
        validators=[MinValueValidator(Decimal("0.00")), MaxValueValidator(Decimal("100.00"))],
    )
    category_over_budget_threshold = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("100.00"),
        validators=[MinValueValidator(Decimal("0.00")), MaxValueValidator(Decimal("100.00"))],
    )
    large_expense_threshold = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("500.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    login_failure_window_minutes = models.PositiveSmallIntegerField(
        default=15, validators=[MinValueValidator(1), MaxValueValidator(1440)]
    )
    login_failure_ip_limit = models.PositiveSmallIntegerField(
        default=5, validators=[MinValueValidator(1), MaxValueValidator(10000)]
    )
    login_failure_global_limit = models.PositiveSmallIntegerField(
        default=20, validators=[MinValueValidator(1), MaxValueValidator(10000)]
    )
    session_idle_timeout_minutes = models.PositiveSmallIntegerField(
        default=60, validators=[MinValueValidator(1), MaxValueValidator(10080)]
    )
    session_absolute_timeout_hours = models.PositiveSmallIntegerField(
        default=24, validators=[MinValueValidator(1), MaxValueValidator(8760)]
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "系统设置"
        verbose_name_plural = "系统设置"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(id=1), name="core_single_system_preference"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    category_warning_threshold__lte=models.F(
                        "category_over_budget_threshold"
                    )
                ),
                name="core_warning_threshold_lte_over_budget",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    login_failure_ip_limit__lte=models.F("login_failure_global_limit")
                ),
                name="core_ip_limit_lte_global_limit",
            ),
        ]

    def __str__(self) -> str:
        return "系统设置"

    def save(self, *args, **kwargs):
        self.pk = self.SINGLETON_ID
        return super().save(*args, **kwargs)


class LoginAttempt(models.Model):
    ip_hash = models.CharField(max_length=64, db_index=True)
    succeeded = models.BooleanField(default=False)
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["ip_hash", "succeeded", "occurred_at"],
                name="core_logina_ip_hash_3d2cff_idx",
            )
        ]
        ordering = ["-occurred_at"]

    def __str__(self) -> str:
        result = "成功" if self.succeeded else "失败"
        return f"登录{result}（{self.occurred_at:%Y-%m-%d %H:%M:%S}）"
