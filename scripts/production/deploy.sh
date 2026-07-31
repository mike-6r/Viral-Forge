#!/usr/bin/env sh
set -eu

test -f .env.production || { echo '.env.production is required' >&2; exit 1; }
test "$(stat -c '%a' .env.production)" = "600" || { echo '.env.production must be mode 0600' >&2; exit 1; }
export VIRALFORGE_PRODUCTION_ENV_FILE=.env.production
./scripts/production/backup.sh
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production build
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production run --rm migrate alembic current
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production run --rm migrate alembic upgrade head
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production run --rm migrate alembic check
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production up -d --wait
