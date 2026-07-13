from decimal import Decimal

from django.db import DatabaseError

from .models import SystemPreference


def budget_thresholds() -> tuple[Decimal, Decimal]:
    try:
        preference = SystemPreference.objects.get(pk=SystemPreference.SINGLETON_ID)
        return preference.category_warning_threshold, preference.category_over_budget_threshold
    except (SystemPreference.DoesNotExist, DatabaseError):
        return Decimal("80.00"), Decimal("100.00")
