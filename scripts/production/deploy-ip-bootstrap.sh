#!/usr/bin/env sh
set -eu

# Run only on the Ubuntu VPS from the repository root. This is temporary HTTP
# bootstrap; it does not enable OAuth or publishing.
test -f .env.ip-bootstrap || { echo '.env.ip-bootstrap is required' >&2; exit 1; }
test "$(stat -c '%a' .env.ip-bootstrap)" = "600" || {
    echo '.env.ip-bootstrap must be mode 0600' >&2
    exit 1
}
export VIRALFORGE_PRODUCTION_ENV_FILE=.env.ip-bootstrap
export VIRALFORGE_IP_BOOTSTRAP_ENV_FILE=.env.ip-bootstrap
compose="docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.ip-bootstrap.yml --env-file .env.ip-bootstrap"
$compose config --quiet
$compose build
$compose run --rm migrate alembic current
$compose run --rm migrate alembic upgrade head
$compose run --rm migrate alembic check
$compose up -d --wait
$compose ps
$compose exec -T api python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').read().decode()); print(urllib.request.urlopen('http://localhost:8000/ready').read().decode())"
$compose exec -T worker celery -A app.worker:celery_app inspect ping --timeout=5
