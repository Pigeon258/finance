from django.db import migrations


def create_default_impulse_tag(apps, schema_editor):
    tag = apps.get_model("ledger", "Tag")
    tag.objects.get_or_create(name="冲动消费")


class Migration(migrations.Migration):
    dependencies = [
        ("ledger", "0003_merchant_tag_transaction_transactiontag_and_more"),
    ]

    operations = [
        migrations.RunPython(create_default_impulse_tag, migrations.RunPython.noop),
    ]
