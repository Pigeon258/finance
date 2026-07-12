from decimal import Decimal

import django.core.validators
from django.db import migrations, models


def create_default_preferences(apps, schema_editor):
    preference = apps.get_model("core", "SystemPreference")
    preference.objects.get_or_create(pk=1)


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="SystemPreference",
            fields=[
                ("id", models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ("time_zone", models.CharField(default="Asia/Shanghai", max_length=64)),
                ("category_warning_threshold", models.DecimalField(decimal_places=2, default=Decimal("80.00"), max_digits=5, validators=[django.core.validators.MinValueValidator(Decimal("0.00")), django.core.validators.MaxValueValidator(Decimal("100.00"))])),
                ("category_over_budget_threshold", models.DecimalField(decimal_places=2, default=Decimal("100.00"), max_digits=5, validators=[django.core.validators.MinValueValidator(Decimal("0.00")), django.core.validators.MaxValueValidator(Decimal("100.00"))])),
                ("large_expense_threshold", models.DecimalField(decimal_places=2, default=Decimal("500.00"), max_digits=14, validators=[django.core.validators.MinValueValidator(Decimal("0.00"))])),
                ("login_failure_window_minutes", models.PositiveSmallIntegerField(default=15, validators=[django.core.validators.MinValueValidator(1)])),
                ("login_failure_ip_limit", models.PositiveSmallIntegerField(default=5, validators=[django.core.validators.MinValueValidator(1)])),
                ("login_failure_global_limit", models.PositiveSmallIntegerField(default=20, validators=[django.core.validators.MinValueValidator(1)])),
                ("session_idle_timeout_minutes", models.PositiveSmallIntegerField(default=60, validators=[django.core.validators.MinValueValidator(1)])),
                ("session_absolute_timeout_hours", models.PositiveSmallIntegerField(default=24, validators=[django.core.validators.MinValueValidator(1)])),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "系统设置",
                "verbose_name_plural": "系统设置",
                "constraints": [
                    models.CheckConstraint(condition=models.Q(("id", 1)), name="core_single_system_preference"),
                    models.CheckConstraint(condition=models.Q(("category_warning_threshold__lte", models.F("category_over_budget_threshold"))), name="core_warning_threshold_lte_over_budget"),
                    models.CheckConstraint(condition=models.Q(("login_failure_ip_limit__lte", models.F("login_failure_global_limit"))), name="core_ip_limit_lte_global_limit"),
                ],
            },
        ),
        migrations.CreateModel(
            name="LoginAttempt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("ip_hash", models.CharField(db_index=True, max_length=64)),
                ("succeeded", models.BooleanField(default=False)),
                ("occurred_at", models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={"ordering": ["-occurred_at"]},
        ),
        migrations.AddIndex(
            model_name="loginattempt",
            index=models.Index(fields=["ip_hash", "succeeded", "occurred_at"], name="core_logina_ip_hash_3d2cff_idx"),
        ),
        migrations.RunPython(create_default_preferences, migrations.RunPython.noop),
    ]
