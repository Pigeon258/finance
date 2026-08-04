#!/bin/sh
set -eu

docker compose build --pull
docker compose up -d db
docker compose run --rm backup python manage.py database_backup --kind deployment
docker compose run --rm web python manage.py maintenance_mode enable

echo "Maintenance mode remains enabled if this script fails; follow the rollback runbook." >&2
docker compose run --rm web python manage.py migrate
docker compose up -d web backup caddy
docker compose exec -T web python manage.py check --deploy --settings=config.settings.production
docker compose exec -T web python manage.py check_financial_integrity
docker compose exec -T web python manage.py check_theme_integrity --strict
docker compose exec -T web python manage.py maintenance_mode disable

echo "Upgrade complete. Verify the dashboard, current credit-card cycle, and theme library manually."
