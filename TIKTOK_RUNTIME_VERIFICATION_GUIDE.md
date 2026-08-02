# TikTok runtime verification guide

Run these commands on the VPS only after the production HTTPS hostname, TikTok developer app, exact callback URI, and encrypted credential-store master key are configured. They do not make a public post.

```bash
cd /root/ViralForge
git pull --ff-only origin main

export VIRALFORGE_PRODUCTION_ENV_FILE=.env.ip-bootstrap
export VIRALFORGE_IP_BOOTSTRAP_ENV_FILE=.env.ip-bootstrap
compose='docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.ip-bootstrap.yml --env-file .env.ip-bootstrap'

./scripts/production/backup.sh
$compose build api worker scheduler discord migrate
$compose run --rm migrate alembic upgrade head
$compose run --rm migrate alembic check
$compose run --rm api python scripts/schema_drift.py /tmp/viralforge-schema-check.db
$compose run --rm api python -m pytest -q
$compose run --rm api python -m ruff check .
$compose run --rm api python -m mypy app
$compose up -d --force-recreate api worker scheduler discord
$compose ps
$compose exec -T api python -c 'from app.common.config import get_settings; from app.publishing.credentials import credential_store; print(credential_store(get_settings()).health())'
$compose exec -T worker celery -A app.worker:celery_app inspect ping --timeout=5
$compose exec -T worker celery -A app.worker:celery_app inspect registered | grep -i tiktok
$compose logs --tail=150 api worker scheduler discord | grep -Ei 'token|secret|upload_url|traceback|error' || true
```

For the current IP-bootstrap profile, confirm that TikTok remains disabled and Discord displays the HTTPS-required guidance. Do not change ports 80 or 443 and do not enable a real TikTok connection in IP-bootstrap mode.

When the trusted domain is active, run one owner-authorized OAuth connection and one **draft upload only**. Do not perform a Direct Post or public upload during this verification. Confirm that the request enters `OPERATOR_COMPLETION_REQUIRED`, then either complete the draft in TikTok manually or record it as rejected/abandoned.
