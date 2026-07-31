#!/usr/bin/env sh
set -eu

# Run every five minutes from the VPS host (systemd timer or cron). It performs
# read-only checks against the private Compose services and sends an optional
# compact Discord-compatible webhook only when the overall state changes.
export VIRALFORGE_PRODUCTION_ENV_FILE="${VIRALFORGE_PRODUCTION_ENV_FILE:-.env.production}"
compose() {
    docker compose -f docker-compose.yml -f docker-compose.prod.yml \
        --env-file "$VIRALFORGE_PRODUCTION_ENV_FILE" "$@"
}
state_dir="${VIRALFORGE_MONITOR_STATE_DIR:-./monitor-state}"
state_file="$state_dir/critical-state"
heartbeat_minutes="${VIRALFORGE_SCHEDULER_STALE_MINUTES:-10}"
mkdir -p "$state_dir"
umask 077

failed=""
check() {
    name="$1"
    shift
    if ! "$@" >/dev/null 2>&1; then
        failed="${failed}${failed:+,}${name}"
    fi
}

check api compose exec -T api python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=5); urllib.request.urlopen('http://localhost:8000/ready', timeout=5)"
check postgres compose exec -T postgres pg_isready -U "${POSTGRES_USER:-viralforge}" -d "${POSTGRES_DB:-viralforge}"
check redis compose exec -T redis redis-cli ping
check worker compose exec -T worker celery -A app.worker:celery_app inspect ping --timeout=5
if ! compose logs --since "${heartbeat_minutes}m" scheduler 2>/dev/null | grep -q 'scheduler-heartbeat'; then
    failed="${failed}${failed:+,}scheduler_stale"
fi

current="ok"
test -z "$failed" || current="critical:$failed"
previous=""
test ! -f "$state_file" || previous="$(cat "$state_file")"
if test "$current" != "$previous"; then
    printf '%s\n' "$current" > "$state_file"
    message="ViralForge monitor: $current"
    printf '%s\n' "$message" >&2
    if test -n "${VIRALFORGE_ALERT_WEBHOOK_URL:-}"; then
        curl --fail --silent --show-error --max-time 10 \
            -H 'Content-Type: application/json' \
            --data "{\"content\":\"$message\"}" \
            "$VIRALFORGE_ALERT_WEBHOOK_URL" >/dev/null
    fi
fi

test "$current" = "ok"
