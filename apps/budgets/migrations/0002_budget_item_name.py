from decimal import Decimal

import django.core.validators
from django.db import migrations, models


def populate_budget_item_names_and_totals(apps, schema_editor):
    CategoryBudget = apps.get_model("budgets", "CategoryBudget")
    MonthlyBudget = apps.get_model("budgets", "MonthlyBudget")

    for item in CategoryBudget.objects.select_related("category"):
        if not item.name:
            item.name = item.category.name
            item.save(update_fields=["name"])

    for budget in MonthlyBudget.objects.all():
        total = sum(
            (
                item.budget_amount
                for item in CategoryBudget.objects.filter(monthly_budget=budget)
            ),
            Decimal("0.00"),
        )
        budget.total_expense_budget = total
        budget.save(update_fields=["total_expense_budget", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [("budgets", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="categorybudget",
            name="name",
            field=models.CharField(default="", max_length=100, verbose_name="预算项目名称"),
        ),
        migrations.AddField(
            model_name="categorybudget",
            name="sort_order",
            field=models.PositiveIntegerField(default=0, verbose_name="显示顺序"),
        ),
        migrations.AlterModelOptions(
            name="categorybudget",
            options={"ordering": ["sort_order", "id"]},
        ),
        migrations.RemoveConstraint(
            model_name="categorybudget",
            name="budgets_unique_month_category",
        ),
        migrations.RunPython(
            populate_budget_item_names_and_totals, migrations.RunPython.noop
        ),
        migrations.AlterField(
            model_name="categorybudget",
            name="budget_amount",
            field=models.DecimalField(
                decimal_places=2,
                max_digits=14,
                validators=[django.core.validators.MinValueValidator(Decimal("0.00"))],
                verbose_name="项目预算金额",
            ),
        ),
        migrations.AlterField(
            model_name="categorybudget",
            name="name",
            field=models.CharField(max_length=100, verbose_name="预算项目名称"),
        ),
        migrations.AddConstraint(
            model_name="categorybudget",
            constraint=models.UniqueConstraint(
                fields=("monthly_budget", "name"),
                name="budgets_unique_month_budget_item",
            ),
        ),
    ]
