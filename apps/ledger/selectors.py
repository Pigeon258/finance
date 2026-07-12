from django.db.models import QuerySet

from .models import Category


def category_list(*, include_inactive: bool = True) -> QuerySet[Category]:
    queryset = Category.objects.all()
    if not include_inactive:
        queryset = queryset.filter(is_active=True)
    return queryset.order_by("category_type", "sort_order", "id")
