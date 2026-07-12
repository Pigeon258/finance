from django.db import migrations


DEFAULT_EXPENSE_CATEGORIES = [
    ("餐饮", "NECESSARY"),
    ("交通", "NECESSARY"),
    ("日用品", "NECESSARY"),
    ("通信", "NECESSARY"),
    ("学习", "NECESSARY"),
    ("医疗", "NECESSARY"),
    ("固定订阅", "NECESSARY"),
    ("娱乐", "FLEXIBLE"),
    ("社交", "FLEXIBLE"),
    ("服饰", "FLEXIBLE"),
    ("数码产品", "FLEXIBLE"),
    ("旅行", "FLEXIBLE"),
    ("其他", "FLEXIBLE"),
]


def create_default_categories(apps, schema_editor):
    category = apps.get_model("ledger", "Category")
    category.objects.get_or_create(
        category_type="INCOME",
        name="其他收入",
        defaults={"sort_order": 10},
    )
    for index, (name, necessity) in enumerate(DEFAULT_EXPENSE_CATEGORIES, start=1):
        category.objects.get_or_create(
            category_type="EXPENSE",
            name=name,
            defaults={"necessity": necessity, "sort_order": index * 10},
        )


class Migration(migrations.Migration):
    dependencies = [("ledger", "0001_initial")]

    operations = [
        migrations.RunPython(create_default_categories, migrations.RunPython.noop),
    ]
