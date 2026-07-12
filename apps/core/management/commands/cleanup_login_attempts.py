from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.models import LoginAttempt
from apps.core.services import LOGIN_ATTEMPT_RETENTION_DAYS


class Command(BaseCommand):
    help = "Delete expired login-attempt audit records"

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=LOGIN_ATTEMPT_RETENTION_DAYS)
        deleted_count, _ = LoginAttempt.objects.filter(occurred_at__lt=cutoff).delete()
        self.stdout.write(f"Deleted {deleted_count} expired login-attempt records.")
