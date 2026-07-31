#!/usr/bin/env sh
set -eu

# Run from the repository root on the VPS.  BACKUP_DIR must be on persistent
# storage and should be copied encrypted to an off-server destination.
compose="docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production"
export VIRALFORGE_PRODUCTION_ENV_FILE="${VIRALFORGE_PRODUCTION_ENV_FILE:-.env.production}"
compose="docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file $VIRALFORGE_PRODUCTION_ENV_FILE"
backup_dir="${BACKUP_DIR:-./backups}"
keep_days="${BACKUP_RETENTION_DAYS:-14}"
mkdir -p "$backup_dir"
umask 077
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="$backup_dir/viralforge-${stamp}.dump"
$compose exec -T postgres pg_dump -U "${POSTGRES_USER:-viralforge}" -Fc -d "${POSTGRES_DB:-viralforge}" > "$archive"
$compose exec -T postgres pg_restore --list < "$archive" >/dev/null
find "$backup_dir" -type f -name 'viralforge-*.dump' -mtime "+$keep_days" -delete
printf '%s\n' "$archive"
