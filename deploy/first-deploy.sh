#!/bin/sh
set -eu

for secret in secrets/django_secret_key secrets/database_password secrets/backup_master_key; do
  test -s "$secret" || { echo "Missing secret: $secret" >&2; exit 1; }
done

mkdir -p backups/business backups/database
docker compose build --pull
docker compose up -d db
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py check --deploy --settings=config.settings.production
docker compose run --rm web python manage.py create_owner
docker compose up -d
docker compose exec -T web python manage.py check_financial_integrity
docker compose run --rm backup python manage.py database_backup --kind manual

echo "Initial deployment complete. Run deploy/verify-deployment.sh next."
