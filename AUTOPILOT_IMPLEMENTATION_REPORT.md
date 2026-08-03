# Autopilot policy, scheduling, and unattended operations

Revision `0032_autopilot_policy_scheduling` adds the durable, brand-scoped foundation for policy-governed unattended work. It extends the existing operations task/alert/report and posting-queue records; it does not introduce a second production queue or publish path.

## Delivered

- Versioned `AutopilotPolicy` records with the four levels: `MANUAL`, `ASSISTED`, `SUPERVISED_AUTOPILOT`, and `AUTOPILOT`.
- A deterministic decision service for every supported automation action. Decisions retain the policy version, evidence, thresholds, actuals, confidence, missing evidence, explanation, and reason codes.
- A centralized exception inbox backed by the existing grouped operator-task system.
- Brand-isolated queue ranking, destination-specific slot reservation, one-active-stage state, stale-job detection, and durable global/per-brand pause controls.
- Bounded Celery task registrations for policy evaluation, discovery, advancement, ranking, schedule handling, provider reconciliation, analytics refresh, watchdog work, and daily briefing generation.
- Brand-scoped API endpoints for policy, decision preview/history, exception inbox, queue ranking, schedule state/reservation, health, and emergency controls.
- Discord Operations summary now displays automation level, pause state, scheduled count, and exceptions without exposing credentials, paths, IDs, or environment values.

## Safety boundary

The default policy is `MANUAL` and disabled. Missing rights, moderation, trust, quality, confidence, destination ownership, scheduling, or provider evidence produces `REQUIRE_REVIEW` and a deduplicated exception. Direct Post is blocked until a provider-specific authorization validator is available. No automated test contacts a provider or creates a public social post.

## VPS deployment procedure

Run the following only on the ViralForge VPS from `/root/ViralForge`. It uses the established IP-bootstrap profile and does not alter MxF Labs or ports `80/443`.

```bash
set -euo pipefail
cd /root/ViralForge
test -f .env.ip-bootstrap && test "$(stat -c '%a' .env.ip-bootstrap)" = 600
export VIRALFORGE_PRODUCTION_ENV_FILE=.env.ip-bootstrap
export VIRALFORGE_IP_BOOTSTRAP_ENV_FILE=.env.ip-bootstrap
COMPOSE='docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.ip-bootstrap.yml --env-file .env.ip-bootstrap'

mkdir -p backups
$COMPOSE exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > "backups/pre-autopilot-$(date -u +%Y%m%dT%H%M%SZ).dump"
BACKUP=$(ls -t backups/pre-autopilot-*.dump | head -n1)
RESTORE_NAME="viralforge-restore-check-$(date +%s)"
docker run -d --rm --name "$RESTORE_NAME" -e POSTGRES_USER=restore -e POSTGRES_PASSWORD=restore -e POSTGRES_DB=restore postgres:17-alpine
until docker exec "$RESTORE_NAME" pg_isready -U restore -d restore >/dev/null; do sleep 1; done
docker run --rm --network "container:$RESTORE_NAME" -v "$PWD/backups:/backups:ro" postgres:17-alpine sh -c "PGPASSWORD=restore pg_restore -h 127.0.0.1 -U restore -d restore /backups/$(basename "$BACKUP")"
docker rm -f "$RESTORE_NAME"

git fetch origin
git status --short
git pull --ff-only origin main
$COMPOSE config --quiet
$COMPOSE build
$COMPOSE run --rm migrate alembic upgrade head
$COMPOSE run --rm api python -m pytest -q
$COMPOSE run --rm api python -m ruff check .
$COMPOSE run --rm api python -m mypy app
$COMPOSE run --rm migrate alembic check
$COMPOSE up -d --wait --force-recreate api worker scheduler discord caddy
$COMPOSE ps
$COMPOSE exec -T api python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').read().decode()); print(urllib.request.urlopen('http://localhost:8000/ready').read().decode())"
$COMPOSE exec -T worker celery -A app.worker:celery_app inspect ping --timeout=5
$COMPOSE exec -T worker celery -A app.worker:celery_app inspect registered | grep -E 'evaluate_autopilot_brands|watchdog_stale_jobs|generate_autopilot_briefings'
test "$($COMPOSE ps -q scheduler | wc -l)" -eq 1
$COMPOSE ps --format 'table {{.Name}}\t{{.Status}}'
$COMPOSE logs --tail=200 api worker scheduler discord | grep -Ei 'token|secret|password' && exit 1 || true
```

The backup check is intentionally non-destructive. Perform a real restore only into a separately provisioned disposable PostgreSQL container before a production restore drill. Keep public publishing disabled throughout this deployment verification.
