# Production operations

Use `docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production` as the production Compose prefix.

- Status/logs: `ps`, `logs -f api`, `logs -f worker`, `logs -f scheduler`.
- Restart one service: `restart worker`. Pause workers: `stop worker`; resume with `up -d worker`.
- Safely disable discovery, publishing, or analytics by setting the corresponding `VIRALFORGE_*_ENABLED=false` values and recreating API/worker/scheduler. Publishing is disabled by default.
- Cleanup: `exec worker celery -A app.worker:celery_app call viralforge.cleanup_expired_media --args='[true]'` for dry-run; omit the argument only after review.
- Monitoring: run `scripts/production/monitor.sh` every five minutes from a systemd timer or cron. It checks API health/readiness, PostgreSQL, Redis, worker ping, and scheduler heartbeat. It records only the current critical state in a protected local state file, so an optional `VIRALFORGE_ALERT_WEBHOOK_URL` is notified once per outage and once on recovery rather than on every run. Treat the webhook as a secret; do not add it to source control.
- Emergency: stop publishing/discovery/workers, revoke preview grants through the authorized API, rotate affected secrets, and disconnect destination accounts. Preserve audit and database records.

Docker uses bounded local log rotation. Application logs redact secret-looking fields and preview token query values. Do not log full transcripts or provider response bodies.

## TikTok pilot

TikTok is disabled by default. Do not enable it in the IP-bootstrap profile: that deployment intentionally blocks OAuth and public publishing. After a trusted HTTPS hostname and registered TikTok callback are in place, configure the provider through protected environment values, keep `VIRALFORGE_TIKTOK_EMERGENCY_PAUSE=true` until the account is verified, and follow [TIKTOK_PILOT_OPERATIONS_GUIDE.md](TIKTOK_PILOT_OPERATIONS_GUIDE.md). Never place TikTok OAuth values in a Compose file, database field, Discord message, shell history, or log.
