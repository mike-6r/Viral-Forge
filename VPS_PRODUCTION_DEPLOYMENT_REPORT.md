# VPS production deployment report

Date: 2026-07-31

## Result

The repository now has a separate, production-safe Ubuntu VPS Compose profile.
It was verified in an isolated local Linux-container stack named
`viralforge-prodtest`. No real VPS, public DNS record, OAuth console, or public
production deployment was available, so none is claimed here.

## Files created or changed

- `docker-compose.prod.yml` - production overlay with Caddy as the sole public
  listener, internal application network, persistent volumes, migration gate,
  scheduler, non-root application services, capability drops, and rotated logs.
- `Caddyfile.production.example` - HTTPS reverse proxy for API and private
  preview endpoints; it does not configure a static media root.
- `.env.production.example` and `config/production-compose-test.env` - secret
  placeholders and a safe disposable verification environment.
- `Dockerfile` - non-root application account and writable data ownership
  support for the production volume initializer.
- `app/common/config.py` and `app/api.py` - production URL, trusted-host,
  CORS, Discord-token, and placeholder database-secret validation, plus proxy
  middleware.
- `app/worker.py` - bounded Celery Beat entries for cleanup, analytics,
  approved publishing requests, discovery polling, and scheduler heartbeat.
- `scripts/production/deploy.sh`, `backup.sh`, `restore-verify.sh`, and
  `monitor.sh` - transparent deployment, backup, disposable restore, and
  de-duplicated host monitoring operations.
- `VPS_DEPLOYMENT_GUIDE.md`, `VPS_SECURITY_CHECKLIST.md`,
  `VPS_BACKUP_RESTORE.md`, and `PRODUCTION_OPERATIONS.md` - owner runbooks.
- `tests/test_settings_logging.py` and `tests/test_production_profile.py` -
  validation, production profile, scheduler, backup, and monitoring coverage.

## Production Compose and networking

The production command is:

```sh
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production
```

Rendered Compose was validated using the disposable environment. The corrected
overlay uses `ports: !reset []` for the API, which removes the development
`8000:8000` mapping during Compose merging. Rendered production ports are only
Caddy `80` and `443`; API, PostgreSQL, Redis, worker, scheduler, Discord, and
storage remain on the private Docker network. PostgreSQL and Redis have no host
port mappings.

Named volumes preserve PostgreSQL data, ViralForge media and model cache,
Celery Beat state, Caddy certificate/configuration state, and backup output.
The production initializer assigns the data volume to UID/GID `10001`; API,
worker, scheduler, and Discord run as that non-root user.

## HTTPS, previews, and OAuth

`Caddyfile.production.example` passed `caddy validate`. In the isolated private
network, a temporary Caddy instance issued a local TLS certificate for
`localhost` and successfully proxied HTTPS `/health` and `/ready` to FastAPI.
The configured production path retains FastAPI-only preview streaming and does
not expose a directory or file server.

Production validation rejects HTTP public-preview and OAuth-callback bases,
wildcard CORS, wildcard/missing trusted hosts, development actors, SQLite,
weak or placeholder secrets, placeholder database passwords, and Discord
enabled without a token. The YouTube callback format is:

```text
https://YOUR_HOST/api/v1/oauth/youtube/callback
```

For a real deployment, set the same real HTTPS host in public, preview, API,
and OAuth callback settings, create its DNS A/AAAA record before Caddy starts,
then register the exact callback URI with YouTube. TikTok was not added.

## Migrations, runtime, and persistence

Production images (`api`, `worker`, `scheduler`, and `migrate`) built without
cache. The isolated PostgreSQL database migrated to the single head
`0021_media_preview`. `alembic check` reported no new upgrade operations.

The stack started PostgreSQL and Redis healthy, API healthy, worker and
scheduler running. Internal API health/readiness both returned success; Celery
inspect ping returned one `pong`; and scheduler logs showed dispatch of the
bounded `scheduler-heartbeat` task. Cleanup was invoked in dry-run mode and
reported zero selections/deletions/failures.

After forced API/worker recreation, test markers in both the model-cache and
media locations remained readable, verifying the named ViralForge data volume
survives container recreation. No publishing setting was enabled and no upload
or publish action was issued.

## Backup and restore

A PostgreSQL custom-format dump was created from the isolated stack, verified
with `pg_restore --list`, restored into only the disposable
`viralforge_restore_verify` database, confirmed at migration
`0021_media_preview`, and then dropped. The active database was never replaced.
The production scripts use timestamped compressed dumps, readable-archive
checks, configurable retention, and an explicit disposable restore target.

Back up `.env.production` only through an encrypted, access-controlled operator
procedure; it contains secrets. Keep OAuth/provider credentials outside normal
database metadata as existing `env://` references, and copy database backups to
an encrypted off-server destination.

## Monitoring, logging, and security

Docker's local logging driver is bounded to 10 MB x 5 files per service.
Application log redaction covers secret-looking values and preview-token query
values. `scripts/production/monitor.sh` checks API health/readiness,
PostgreSQL, Redis, worker ping, and a recent scheduler heartbeat. It stores the
last critical state locally and sends an optional Discord-compatible webhook
only on state change, including recovery, preventing repeated alert spam.

The security checklist covers Ubuntu LTS patching/NTP, non-root SSH-key access,
UFW rules (SSH/80/443 only), disabled public database/cache ports, strict
production-environment permissions, Docker group risk, optional fail2ban,
credential rotation, and emergency revocation. No Docker socket or privileged
container is used; application services drop Linux capabilities and prevent new
privileges.

## Quality and configuration verification

- `docker compose ... config --quiet`: passed.
- `caddy validate`: passed (format-only warning from Caddy is non-functional).
- `python -m pytest -q`: 109 passed; two upstream deprecation warnings only.
- `python -m ruff check .`: passed.
- `python -m mypy app`: passed, 71 source files.
- `python scripts/schema_drift.py`: no schema drift detected.
- production-container `alembic check`: no new upgrade operations detected.

## VPS owner actions still required

1. Provision an Ubuntu LTS VPS and install Docker Engine/Compose from Docker's
   official repository.
2. Configure the host firewall, SSH hardening, time synchronization, and
   security updates using `VPS_SECURITY_CHECKLIST.md`.
3. Create DNS for the chosen real hostname and replace every placeholder in
   `.env.production`; set mode `0600`.
4. Configure the real Caddy hostname, secure backup destination, and optional
   alert webhook, then run `scripts/production/deploy.sh`.
5. Verify the real public HTTPS preview/range flow and exact OAuth callback in
   the provider console. No live DNS, TLS certificate, Discord production
   connection, or OAuth account was exercised locally.
