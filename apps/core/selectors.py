from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.contrib.auth import SESSION_KEY
from django.contrib.sessions.models import Session
from django.db import DatabaseError
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .middleware import SESSION_CREATED_AT, SESSION_LAST_ACTIVITY_AT
from .models import SystemPreference


@dataclass(frozen=True)
class SessionSummary:
    reference: str
    is_current: bool
    created_at: datetime | None
    last_activity_at: datetime | None
    expire_date: datetime


def session_reference(session_key: str) -> str:
    return salted_hmac("personal-finance.session-reference", session_key).hexdigest()[:24]


def _timestamp(value) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.get_current_timezone())
    except (TypeError, ValueError, OSError):
        return None


def active_owner_sessions(*, owner_id: int, current_session_key: str) -> tuple[SessionSummary, ...]:
    rows = []
    for session in Session.objects.filter(expire_date__gt=timezone.now()).order_by("-expire_date"):
        try:
            data = session.get_decoded()
        except Exception:
            continue
        if str(data.get(SESSION_KEY, "")) != str(owner_id):
            continue
        rows.append(
            SessionSummary(
                reference=session_reference(session.session_key),
                is_current=session.session_key == current_session_key,
                created_at=_timestamp(data.get(SESSION_CREATED_AT)),
                last_activity_at=_timestamp(data.get(SESSION_LAST_ACTIVITY_AT)),
                expire_date=session.expire_date,
            )
        )
    return tuple(sorted(rows, key=lambda row: (not row.is_current, row.expire_date)))


def budget_thresholds() -> tuple[Decimal, Decimal]:
    try:
        preference = SystemPreference.objects.get(pk=SystemPreference.SINGLETON_ID)
        return preference.category_warning_threshold, preference.category_over_budget_threshold
    except (SystemPreference.DoesNotExist, DatabaseError):
        return Decimal("80.00"), Decimal("100.00")


def large_expense_threshold() -> Decimal:
    try:
        return SystemPreference.objects.values_list("large_expense_threshold", flat=True).get(
            pk=SystemPreference.SINGLETON_ID
        )
    except (SystemPreference.DoesNotExist, DatabaseError):
        return Decimal("500.00")
