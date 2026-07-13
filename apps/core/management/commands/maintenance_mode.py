from django.core.management.base import BaseCommand

from apps.core.models import MaintenanceState


class Command(BaseCommand):
    help = "查看、启用或停用数据库维护模式"

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["status", "enable", "disable"])

    def handle(self, *args, **options):
        state, _ = MaintenanceState.objects.get_or_create(pk=MaintenanceState.SINGLETON_ID)
        action = options["action"]
        if action == "enable":
            state.enabled = True
            state.save(update_fields=["enabled", "updated_at"])
        elif action == "disable":
            state.enabled = False
            state.save(update_fields=["enabled", "updated_at"])
        self.stdout.write("enabled" if state.enabled else "disabled")
