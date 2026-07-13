#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 db-*.dump.enc" >&2
  exit 2
fi

filename=$(basename "$1")
case "$filename" in
  db-*.dump.enc) ;;
  *) echo "Invalid database backup filename." >&2; exit 2 ;;
esac
test -f "backups/database/$filename" || { echo "Backup file not found." >&2; exit 1; }

docker compose run --rm backup python manage.py database_backup --kind manual
docker compose run --rm web python manage.py maintenance_mode enable
docker compose stop caddy web backup

echo "If restore fails, the safety backup is retained and pg_restore rolls back atomically." >&2
docker compose run --rm backup \
  python manage.py database_restore "/backups/database/$filename" --confirm-restore
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py clearsessions
docker compose run --rm web python manage.py check_financial_integrity
docker compose run --rm web python manage.py maintenance_mode disable
docker compose up -d web backup caddy

echo "Database restore complete. Sign in and perform the manual checks in the runbook."
