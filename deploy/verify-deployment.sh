#!/bin/sh
set -eu

domain=${APP_DOMAIN:?export APP_DOMAIN before running this script}

docker compose ps --status running
docker compose exec -T web python manage.py check --deploy --settings=config.settings.production
docker compose exec -T web python manage.py check_financial_integrity

db_container=$(docker compose ps -q db)
web_container=$(docker compose ps -q web)
db_bindings=$(docker inspect --format '{{json (index .NetworkSettings.Ports "5432/tcp")}}' "$db_container")
web_bindings=$(docker inspect --format '{{json (index .NetworkSettings.Ports "8000/tcp")}}' "$web_container")

test "$db_bindings" = "null" || { echo "Database port is published." >&2; exit 1; }
test "$web_bindings" = "null" || { echo "Web port is published." >&2; exit 1; }

headers=$(curl --fail --silent --show-error --head "https://$domain/")
echo "$headers" | grep -qi '^strict-transport-security:'
echo "$headers" | grep -qi '^content-security-policy:'
echo "$headers" | grep -qi '^x-content-type-options: nosniff'
echo "$headers" | grep -qi '^x-frame-options: DENY'

status=$(curl --silent --output /dev/null --write-out '%{http_code}' "https://$domain/health/live")
test "$status" = "404" || { echo "Health endpoint is publicly routed." >&2; exit 1; }

echo "Deployment checks passed. Reboot/recovery and restore drills remain manual runbook steps."
