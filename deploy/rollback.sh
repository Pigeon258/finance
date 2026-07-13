#!/bin/sh
set -eu

if [ "$#" -ne 3 ]; then
  echo "Usage: $0 WEB_IMAGE CADDY_IMAGE MAINTENANCE_IMAGE" >&2
  exit 2
fi

export WEB_IMAGE="$1"
export CADDY_IMAGE="$2"
export MAINTENANCE_IMAGE="$3"

docker compose run --rm web python manage.py maintenance_mode enable
docker compose up -d --no-build web backup caddy
docker compose exec -T web python manage.py check --deploy --settings=config.settings.production
docker compose exec -T web python manage.py check_financial_integrity
docker compose exec -T web python manage.py maintenance_mode disable

echo "Image rollback complete. This script does not reverse incompatible migrations."
