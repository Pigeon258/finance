#!/bin/sh
set -eu

expected_sha=${1:-}
task=${QUICK_RELEASE_TASK:-}

if [ -z "$expected_sha" ] || [ -z "$task" ]; then
  echo "Usage: QUICK_RELEASE_TASK=QUICK-ITERATION-NN sh deploy/quick-upgrade.sh FULL_GIT_SHA" >&2
  exit 2
fi

case "$task" in
  QUICK-ITERATION-[0-9][0-9]) ;;
  *)
    echo "Quick release requires a QUICK-ITERATION-NN task." >&2
    exit 2
    ;;
esac

actual_sha=$(git rev-parse HEAD)
test "$actual_sha" = "$expected_sha" || {
  echo "Checked-out SHA does not match the reviewed release SHA." >&2
  exit 1
}

test -z "$(git status --porcelain --untracked-files=no)" || {
  echo "Tracked production files are modified; refusing quick release." >&2
  exit 1
}

rollback_id=$(date -u +%Y%m%dT%H%M%SZ)
web_container=$(docker compose ps -q web)
caddy_container=$(docker compose ps -q caddy)
backup_container=$(docker compose ps -q backup)

test -n "$web_container" && test -n "$caddy_container" && test -n "$backup_container" || {
  echo "Existing web, caddy, and backup containers are required for a quick release." >&2
  exit 1
}

web_image=$(docker inspect --format '{{.Image}}' "$web_container")
caddy_image=$(docker inspect --format '{{.Image}}' "$caddy_container")
maintenance_image=$(docker inspect --format '{{.Image}}' "$backup_container")
docker image tag "$web_image" "personal-finance-web:quick-rollback-$rollback_id"
docker image tag "$caddy_image" "personal-finance-caddy:quick-rollback-$rollback_id"
docker image tag "$maintenance_image" "personal-finance-maintenance:quick-rollback-$rollback_id"

docker compose build web caddy
docker compose run --rm backup python manage.py database_backup --kind deployment
docker compose up -d --wait --wait-timeout 90 --no-deps web caddy
docker compose exec -T web python manage.py check --deploy --settings=config.settings.production
docker compose exec -T web python manage.py check_financial_integrity
docker compose exec -T web python manage.py check_theme_integrity --strict

echo "Quick release complete for $task at $actual_sha."
echo "Rollback web image: personal-finance-web:quick-rollback-$rollback_id"
echo "Rollback caddy image: personal-finance-caddy:quick-rollback-$rollback_id"
echo "Rollback maintenance image: personal-finance-maintenance:quick-rollback-$rollback_id"
