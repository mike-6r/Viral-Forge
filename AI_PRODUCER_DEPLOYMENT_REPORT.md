# AI Producer deployment report

Status: pending real VPS execution. This document deliberately records no fabricated deployment result.

Deployment commit: `943d3e3` (plus the Discord review/control and confidence-calibration follow-up in this repository).

Migration: `0027_ai_producer_recommendations`.

Run the following from `/root/ViralForge` on the VPS. It uses the active IP-bootstrap profile and does not touch ports 80/443, Docker volumes, media, `.env.ip-bootstrap`, or the credential store.

```bash
cd /root/ViralForge
git status --short
git diff -- scripts/production/deploy-ip-bootstrap.sh
git fetch origin
git log --oneline HEAD..origin/main

export VIRALFORGE_PRODUCTION_ENV_FILE=.env.ip-bootstrap
export VIRALFORGE_IP_BOOTSTRAP_ENV_FILE=.env.ip-bootstrap
compose() { docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.ip-bootstrap.yml --env-file .env.ip-bootstrap "$@"; }

BACKUP_ARCHIVE="$(./scripts/production/backup.sh)"
test -s "$BACKUP_ARCHIVE"
compose exec -T postgres pg_restore --list < "$BACKUP_ARCHIVE" >/dev/null
printf 'Verified backup: %s\n' "$BACKUP_ARCHIVE"

git pull --ff-only origin main
git merge-base --is-ancestor 943d3e3 HEAD
git log -1 --oneline

compose build api worker scheduler discord migrate
compose run --rm migrate alembic upgrade head
compose run --rm migrate alembic current
compose run --rm migrate alembic heads
compose run --rm migrate alembic check
compose run --rm api python scripts/schema_drift.py /tmp/ai-producer-schema.db

compose up -d --force-recreate api worker scheduler discord
compose ps
compose exec -T api python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').read().decode()); print(urllib.request.urlopen('http://localhost:8000/ready').read().decode())"
compose exec -T worker celery -A app.worker:celery_app inspect ping --timeout=5
compose exec -T worker celery -A app.worker:celery_app inspect registered --timeout=5
compose exec -T worker celery -A app.worker:celery_app inspect active_queues --timeout=5
compose logs --tail=120 worker scheduler discord
```

Success criteria:

- one Alembic head: `0027_ai_producer_recommendations`;
- no schema drift and `alembic check` succeeds;
- API readiness, PostgreSQL, Redis, worker, scheduler, and Discord are healthy;
- registered tasks include `viralforge.generate_producer_recommendations`, `viralforge.generate_clip_quality_report`, and `viralforge.evaluate_producer_predictions`;
- one worker consumer and one scheduler service are running, with no restart loop or task-import error.

