from django.core.management.base import BaseCommand, CommandError

from apps.core.models import SystemPreference
from apps.core.themes import THEME_CONTRACT_VERSION, THEME_SCHEMA_VERSION, get_theme_registry


class Command(BaseCommand):
    help = "校验内置/运行时主题并确认当前主题无需回退。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--strict",
            action="store_true",
            help="发现无效目录或活动主题回退时返回失败。",
        )

    def handle(self, *args, **options):
        registry = get_theme_registry()
        safe_default = registry.get("safe-default")
        if safe_default is None or safe_default.root is None or safe_default.source != "builtin":
            raise CommandError("安全默认主题包不可用。")

        preference, _ = SystemPreference.objects.get_or_create(
            pk=SystemPreference.SINGLETON_ID
        )
        selection = registry.select(
            preference.active_theme_id,
            preference.last_known_good_theme_id,
        )
        problems = []
        if registry.errors:
            problems.append(f"存在 {len(registry.errors)} 个未注册主题目录")
        if selection.theme.id != preference.active_theme_id:
            problems.append(
                f"活动主题 {preference.active_theme_id} 已回退为 {selection.theme.id}"
            )

        if problems and options["strict"]:
            raise CommandError("；".join(problems))
        for problem in problems:
            self.stdout.write(self.style.WARNING(problem))
        self.stdout.write(
            self.style.SUCCESS(
                "主题完整性检查通过："
                f"active={preference.active_theme_id} resolved={selection.theme.id} "
                f"schema={THEME_SCHEMA_VERSION} contract={THEME_CONTRACT_VERSION}"
            )
        )
