from decimal import Decimal

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Account",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, verbose_name="名称")),
                ("account_type", models.CharField(choices=[("BANK", "银行卡"), ("WECHAT", "微信余额"), ("ALIPAY", "支付宝余额"), ("CREDIT_CARD", "信用卡")], max_length=20, verbose_name="账户类型")),
                ("balance_nature", models.CharField(choices=[("ASSET", "资产"), ("LIABILITY", "负债")], max_length=12, verbose_name="余额性质")),
                ("initial_balance", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=14, validators=[django.core.validators.MinValueValidator(Decimal("0.00"))], verbose_name="初始余额")),
                ("is_active", models.BooleanField(default=True, verbose_name="启用")),
                ("sort_order", models.PositiveIntegerField(default=0, verbose_name="显示顺序")),
                ("opened_at", models.DateField(blank=True, null=True, verbose_name="开户日期")),
                ("note", models.TextField(blank=True, verbose_name="备注")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["sort_order", "id"],
                "indexes": [models.Index(fields=["is_active", "sort_order"], name="accounts_ac_is_acti_24960f_idx")],
                "constraints": [
                    models.CheckConstraint(condition=models.Q(("initial_balance__gte", Decimal("0.00"))), name="accounts_initial_balance_nonnegative"),
                    models.CheckConstraint(condition=models.Q(models.Q(("account_type", "CREDIT_CARD"), ("balance_nature", "LIABILITY")), models.Q(models.Q(("account_type", "CREDIT_CARD"), _negated=True), ("balance_nature", "ASSET")), _connector="OR"), name="accounts_type_matches_balance_nature"),
                    models.UniqueConstraint(condition=models.Q(("account_type", "CREDIT_CARD"), ("is_active", True)), fields=("account_type",), name="accounts_one_active_credit_card"),
                ],
            },
        ),
    ]
