from django.db import migrations, models


def activate_aurora_ledger(apps, schema_editor):
    # 仅升级仍在使用安全默认主题的实例，不覆盖用户已经选择的主题。
    preference = apps.get_model("core", "SystemPreference")
    preference.objects.filter(active_theme_id="safe-default").update(
        active_theme_id="aurora-ledger"
    )


def restore_safe_default(apps, schema_editor):
    preference = apps.get_model("core", "SystemPreference")
    preference.objects.filter(active_theme_id="aurora-ledger").update(
        active_theme_id="safe-default"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0005_systempreference_active_theme_id_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="systempreference",
            name="active_theme_id",
            field=models.CharField(default="aurora-ledger", max_length=80),
        ),
        migrations.RunPython(activate_aurora_ledger, restore_safe_default),
    ]
