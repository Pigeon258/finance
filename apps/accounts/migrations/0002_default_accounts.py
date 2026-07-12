from django.db import migrations


DEFAULT_ACCOUNTS = [
    ("银行卡", "BANK", "ASSET", 10),
    ("微信余额", "WECHAT", "ASSET", 20),
    ("支付宝余额", "ALIPAY", "ASSET", 30),
    ("信用卡", "CREDIT_CARD", "LIABILITY", 40),
]


def create_default_accounts(apps, schema_editor):
    account = apps.get_model("accounts", "Account")
    for name, account_type, balance_nature, sort_order in DEFAULT_ACCOUNTS:
        account.objects.get_or_create(
            account_type=account_type,
            defaults={
                "name": name,
                "balance_nature": balance_nature,
                "sort_order": sort_order,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial")]

    operations = [
        migrations.RunPython(create_default_accounts, migrations.RunPython.noop),
    ]
