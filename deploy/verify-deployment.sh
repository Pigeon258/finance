#!/bin/sh
set -eu

domain=${APP_DOMAIN:?export APP_DOMAIN before running this script}

docker compose ps --status running
docker compose exec -T web python manage.py check --deploy --settings=config.settings.production
docker compose exec -T web python manage.py check_financial_integrity
docker compose exec -T web python manage.py check_theme_integrity --strict

db_container=$(docker compose ps -q db)
web_container=$(docker compose ps -q web)
db_bindings=$(docker inspect --format '{{json (index .NetworkSettings.Ports "5432/tcp")}}' "$db_container")
web_bindings=$(docker inspect --format '{{json (index .NetworkSettings.Ports "8000/tcp")}}' "$web_container")
web_theme_mount=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/app/var/themes"}}{{.RW}}{{end}}{{end}}' "$web_container")
caddy_container=$(docker compose ps -q caddy)
caddy_theme_mount=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/srv/themes"}}{{.RW}}{{end}}{{end}}' "$caddy_container")

test "$db_bindings" = "null" || { echo "Database port is published." >&2; exit 1; }
test "$web_bindings" = "null" || { echo "Web port is published." >&2; exit 1; }
test "$web_theme_mount" = "true" || { echo "Web theme volume is not writable." >&2; exit 1; }
test "$caddy_theme_mount" = "false" || { echo "Caddy theme volume is not read-only." >&2; exit 1; }

headers=$(curl --fail --silent --show-error --head "https://$domain/")
echo "$headers" | grep -qi '^strict-transport-security:'
echo "$headers" | grep -qi '^content-security-policy:'
echo "$headers" | grep -qi '^x-content-type-options: nosniff'
echo "$headers" | grep -qi '^x-frame-options: DENY'

app_css_headers=$(curl --fail --silent --show-error --head "https://$domain/static/css/app.css")
echo "$app_css_headers" | grep -qi '^content-type: text/css'
echo "$app_css_headers" | grep -qi '^cache-control: public, max-age=3600, must-revalidate'
echo "$app_css_headers" | grep -qi '^x-content-type-options: nosniff'

theme_headers=$(curl --fail --silent --show-error --head "https://$domain/static/themes/aurora-ledger/theme.css")
echo "$theme_headers" | grep -qi '^content-type: text/css'
echo "$theme_headers" | grep -qi '^cache-control: public, max-age=31536000, immutable'
echo "$theme_headers" | grep -qi '^x-content-type-options: nosniff'

missing_theme_status=$(curl --silent --output /dev/null --write-out '%{http_code}' "https://$domain/themes/not-installed/theme.css")
test "$missing_theme_status" = "404" || { echo "Unknown runtime theme asset is exposed." >&2; exit 1; }

status=$(curl --silent --output /dev/null --write-out '%{http_code}' "https://$domain/health/live")
test "$status" = "404" || { echo "Health endpoint is publicly routed." >&2; exit 1; }

echo "Deployment checks passed. Reboot/recovery and restore drills remain manual runbook steps."
