from django import forms

from .models import Category


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = [
            "name",
            "category_type",
            "necessity",
            "default_budget",
            "is_active",
            "sort_order",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["category_type"].disabled = True
            self.fields["category_type"].help_text = "分类创建后不能修改收入/支出类型。"

    def clean(self):
        cleaned_data = super().clean()
        category_type = cleaned_data.get("category_type")
        necessity = cleaned_data.get("necessity")
        if category_type == Category.CategoryType.EXPENSE and not necessity:
            self.add_error("necessity", "支出分类必须选择消费性质。")
        if category_type == Category.CategoryType.INCOME:
            cleaned_data["necessity"] = None
        return cleaned_data
