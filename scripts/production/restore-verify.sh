#!/usr/bin/env sh
set -eu

archive="${1:?usage: restore-verify.sh path/to/backup.dump}"
export VIRALFORGE_PRODUCTION_ENV_FILE="${VIRALFORGE_PRODUCTION_ENV_FILE:-.env.production}"
compose="docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file $VIRALFORGE_PRODUCTION_ENV_FILE"
verify_db="viralforge_restore_verify"
$compose exec -T postgres pg_restore --list < "$archive" >/dev/null
$compose exec -T postgres dropdb -U "${POSTGRES_USER:-viralforge}" --if-exists "$verify_db"
$compose exec -T postgres createdb -U "${POSTGRES_USER:-viralforge}" "$verify_db"
trap '$compose exec -T postgres dropdb -U "${POSTGRES_USER:-viralforge}" --if-exists "$verify_db"' EXIT
$compose exec -T postgres pg_restore -U "${POSTGRES_USER:-viralforge}" -d "$verify_db" < "$archive"
$compose exec -T postgres psql -U "${POSTGRES_USER:-viralforge}" -d "$verify_db" -c 'SELECT 1' >/dev/null
echo "Disposable restore verification passed."
