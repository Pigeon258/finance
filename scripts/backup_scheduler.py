import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from datetime import time as clock_time
from pathlib import Path
from zoneinfo import ZoneInfo


def parse_clock(value: str) -> clock_time:
    try:
        return clock_time.fromisoformat(value)
    except ValueError as error:
        raise SystemExit(f"Invalid scheduler time: {value}") from error


def next_occurrence(now: datetime, target: clock_time, *, weekday: int | None = None) -> datetime:
    candidate = now.replace(hour=target.hour, minute=target.minute, second=0, microsecond=0)
    if weekday is None:
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate
    days_ahead = (weekday - candidate.weekday()) % 7
    candidate += timedelta(days=days_ahead)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def run_manage(*arguments: str) -> bool:
    completed = subprocess.run([sys.executable, "manage.py", *arguments], cwd="/app", check=False)
    return completed.returncode == 0


def cleanup_pre_restore_backups(now: datetime) -> None:
    directory = Path(os.environ.get("BUSINESS_BACKUP_DIR", "/backups/business"))
    cutoff = now.timestamp() - 7 * 24 * 60 * 60
    for backup_file in directory.glob("pre-restore-*.pfbackup"):
        if backup_file.stat().st_mtime < cutoff:
            backup_file.unlink()


def main() -> None:
    timezone = ZoneInfo(os.environ.get("APP_TIME_ZONE", "Asia/Shanghai"))
    daily_time = parse_clock(os.environ.get("BACKUP_DAILY_TIME", "02:30"))
    weekly_time = parse_clock(os.environ.get("BACKUP_WEEKLY_TIME", "03:00"))
    Path("/tmp/backup-scheduler-heartbeat").touch()
    while True:
        now = datetime.now(timezone)
        daily = next_occurrence(now, daily_time)
        weekly = next_occurrence(now, weekly_time, weekday=6)
        next_run = min(daily, weekly)
        time.sleep(max((next_run - now).total_seconds(), 1))
        now = datetime.now(timezone)
        if next_run == daily:
            run_manage("database_backup", "--kind", "daily")
            run_manage("clearsessions")
            run_manage("cleanup_login_attempts")
            run_manage("cleanup_import_files")
            cleanup_pre_restore_backups(now)
        else:
            run_manage("database_backup", "--kind", "weekly")


if __name__ == "__main__":
    main()
