from django.core.management.base import BaseCommand, CommandError

from apps.core.integrity import financial_integrity_issues


class Command(BaseCommand):
    help = "检查核心账本、退款和分期关联的财务完整性"

    def handle(self, *args, **options):
        issues = financial_integrity_issues()
        if issues:
            detail = "\n".join(f"[{issue.code}] {issue.message}" for issue in issues)
            raise CommandError(f"财务完整性检查失败：\n{detail}")
        self.stdout.write(self.style.SUCCESS("财务完整性检查通过。"))
