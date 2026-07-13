from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.imports.services import cleanup_stale_import_files


class Command(BaseCommand):
    help = "Delete import files that have survived longer than 24 hours"

    def handle(self, *args, **options):
        cleaned = cleanup_stale_import_files(before=timezone.now() - timedelta(hours=24))
        self.stdout.write(self.style.SUCCESS(f"Cleaned {cleaned} stale import file(s)."))
