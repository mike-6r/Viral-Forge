# ViralForge Project Guide

ViralForge is a review-first short-form media operations platform. It helps an
operator discover or add an authorized source, prepare a clip, review the
creative output, build an editable content package, and make an explicit
publishing decision. It does not silently publish content.

This is the starting point for operating and developing the repository. It is
an index and practical guide; detailed security, provider, and deployment
documents are linked throughout.

## 1. What the system does

For every brand, ViralForge can:

- ingest approved public sources, manual video URLs, RSS feeds, websites, and
  manual uploads through bounded safety checks;
- create a `ProductionProject`, download authorized media, analyze it, and
  propose clip opportunities;
- render approved opportunities into reviewable clips with a private preview;
- generate evidence-bound, editable content packages from persisted source
  metadata, transcript segments, opportunities, and analysis events;
- require explicit review of clips and content packages before material reaches
  a posting queue;
- support publishing foundations, destination accounts, schedules, analytics,
  operator feedback, and manual-publication records;
- run the operator workflow from Discord as well as through FastAPI.

The system is multi-workspace and multi-brand. A workspace owns brands; a brand
owns its sources, projects, analysis, clips, packages, queue records, accounts,
and audit events. Never use one brand’s records as another brand’s content.

## 2. Safety model

ViralForge is intentionally approval-first.

- Public availability does **not** mean reuse rights are granted.
- Source acceptance, rights requirements, moderation requirements, clip
  approval, and content-package approval are distinct gates.
- A queue item marked `READY_TO_POST` is not an automatic upload. It means its
  creative work has already been approved and is waiting for an explicit human
  publishing decision.
- Destination accounts contain provider/account references and credential
  reference IDs only. Raw OAuth tokens and other secrets must remain in the
  configured credential boundary, never in ordinary database fields, commits,
  Discord messages, or logs.
- Preview and full-quality URLs are private, short-lived, access-limited token
  URLs. Do not expose Docker volumes or media directories as static web roots.
- Automated tests use mocked providers and must never make public uploads.

Read [docs/SECURITY.md](docs/SECURITY.md),
[docs/RIGHTS_AND_ATTRIBUTION.md](docs/RIGHTS_AND_ATTRIBUTION.md),
[docs/MODERATION_MODEL.md](docs/MODERATION_MODEL.md), and
[CREDENTIAL_STORE_OPERATIONS_GUIDE.md](CREDENTIAL_STORE_OPERATIONS_GUIDE.md)
before enabling provider credentials.

## 3. Architecture

The application is a modular Python monolith:

| Component | Responsibility |
| --- | --- |
| FastAPI (`app/api.py`) | HTTP API, health/readiness, authenticated operator APIs |
| PostgreSQL | durable product, workflow, audit, publishing, and analytics state |
| Redis | Celery broker and result backend |
| Celery worker | bounded downloading, analysis, rendering, content-package, publishing, and refresh jobs |
| Celery scheduler | recurring operational, discovery, publishing, and analytics checks |
| Discord bot (`app/discord_bot.py`) | operator control center, review views, setup and support controls |
| Caddy / Apache proxy | HTTPS edge proxy; production services stay private |
| Docker volumes | PostgreSQL data, media, model cache, scheduler state, Caddy state |

Domain rules live in the relevant `app/<domain>/service.py` module. SQLAlchemy
models persist state; Alembic migrations are the schema authority. Do not
modify released migrations. Create a new forward-only migration for every
production schema change.

Important domains include `brands`, `discovery`, `production`, `analysis`,
`opportunities`, `content_packages`, `publishing`, `manual_publishing`,
`analytics`, `autopilot`, `operations`, `audit`, and `discord_business`.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
[docs/DOMAIN_MODEL.md](docs/DOMAIN_MODEL.md), and
[docs/MIGRATION_POLICY.md](docs/MIGRATION_POLICY.md).

## 4. End-to-end workflow

1. **Choose a brand.** Discord and API actions use the selected/default brand.
2. **Find or add a source.** Use an approved discovery source, a verified
   public reference, or a manual video URL you are authorized to process.
3. **Accept the source.** The project moves from source review to preparation.
4. **Prepare and analyze.** The worker downloads, inspects, transcribes, and
   analyzes the authorized source.
5. **Review suggestions.** Approve or reject each proposed opportunity.
6. **Review finished clips.** A successful render is not approved until an
   operator decides on it.
7. **Review the content package.** Edit or approve its platform-specific title,
   captions, hashtags, attribution, and warnings. Generated language is
   separated from verified facts and transcript-derived statements.
8. **Make a publishing decision.** An approved package can create or update a
   queue item, but no public upload happens without an explicit destination and
   manual publish/schedule confirmation.
9. **Ingest analytics.** Store only official or operator-imported facts. The
   system produces recommendations; it does not silently alter production
   settings.

### Creative review consistency

`/viralforge review` and Operations use the same brand-scoped review inbox:

1. source acceptance (`SOURCE_REVIEW_REQUIRED`)
2. pending clip opportunities
3. successful clips with `approval_status=PENDING`
4. pending content packages
5. discovered media in `NEEDS_REVIEW`

Queue-ready posts and approved content packages are intentionally not creative
review items. Operations reports them separately as queue/content-ready work.
The scheduler and live Operations view close a stale `REVIEW_CONTENT` task
once the review inbox reaches zero.

See [REVIEW_STATE_CONSISTENCY_REPORT.md](REVIEW_STATE_CONSISTENCY_REPORT.md).

## 5. Discord operator guide

The Discord bot is an operator surface, not an autonomous publisher.

- `/viralforge home` opens the selected brand’s Operations Center.
- `/viralforge review` opens the next actionable creative decision.
- `/viralforge project` and **Continue Working** show a project’s current
  stage and safe next action.
- `/viralforge operations` shows brand health, creative review count,
  queue health, current tasks, and automation state.
- `/viralforge brands` selects the active brand.
- `/viralforge ready-to-post` displays queue items; it does not publish them.
- `/discovery` commands manage reviewable discovery work and approved sources.
- `/setup` and `/admin` commands manage the business Discord experience and
  require server-owner/operator permissions.

Discord access to private operations requires a member role listed in
`VIRALFORGE_DISCORD_ALLOWED_ROLE_IDS`. Public/community access does not grant
production or publishing authority.

Use [DISCORD_COMMAND_REFERENCE.md](DISCORD_COMMAND_REFERENCE.md),
[OPERATOR_COMMAND_GUIDE.md](OPERATOR_COMMAND_GUIDE.md),
[DAILY_OPERATIONS_GUIDE.md](DAILY_OPERATIONS_GUIDE.md), and
[DISCORD_PERMISSION_MATRIX.md](DISCORD_PERMISSION_MATRIX.md).

## 6. Local development

Prerequisites: Python 3.12, Docker Desktop or Docker Engine, Git, FFmpeg,
FFprobe, and `yt-dlp` for real media work.

```powershell
Copy-Item .env.example .env
./scripts/dev.ps1 build
./scripts/dev.ps1 up
./scripts/dev.ps1 migrate
./scripts/dev.ps1 status
```

Useful checks:

```powershell
./scripts/dev.ps1 test
./scripts/dev.ps1 lint
./scripts/dev.ps1 typecheck
Invoke-WebRequest http://localhost:8000/health
Invoke-WebRequest http://localhost:8000/ready
```

Use `./scripts/dev.ps1 down` to stop local services. `reset -Force` destroys
local Docker data and must never be used for production. For Linux/macOS,
equivalent `make` commands are documented in
[docs/LOCAL_DEVELOPMENT.md](docs/LOCAL_DEVELOPMENT.md).

## 7. Configuration

`.env` is local-only and must not be committed. Start from `.env.example` for
development or `.env.production.example` / `.env.ip-bootstrap.example` for a
VPS profile.

Configure, without printing secrets:

- PostgreSQL, Redis, Celery broker/result URLs
- API and preview signing secrets
- `VIRALFORGE_PUBLIC_BASE_URL`, `VIRALFORGE_API_BASE_URL`, and trusted hosts
- Discord token, guild ID, review channel ID, and allowed role IDs
- optional YouTube discovery API key
- optional approved OAuth/provider settings and credential-store references
- FFmpeg/FFprobe paths and persistent storage roots

Set environment-file permissions to owner-read/write only on the VPS:

```bash
chmod 600 .env.ip-bootstrap   # or .env.production
```

## 8. Production deployment

The current VPS uses Apache for the existing MxF Labs site on public ports
80/443 and runs ViralForge Caddy only on `127.0.0.1:8081`. Apache forwards
`viralforge.mxf-labs.com` to that loopback service. Do not change the MxF Labs
virtual host or expose PostgreSQL, Redis, the API, worker, or Discord bot.

The active ViralForge Compose profile is:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.prod.yml \
  -f docker-compose.apache-proxy.yml \
  --env-file .env.ip-bootstrap
```

Safe update sequence on the VPS:

```bash
cd /root/ViralForge
git status --short
git pull --ff-only origin main

export VIRALFORGE_PRODUCTION_ENV_FILE=.env.ip-bootstrap
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  -f docker-compose.apache-proxy.yml --env-file .env.ip-bootstrap \
  up -d --build --force-recreate

docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  -f docker-compose.apache-proxy.yml --env-file .env.ip-bootstrap ps
curl -fsS https://viralforge.mxf-labs.com/health
curl -fsS https://viralforge.mxf-labs.com/ready
```

This preserves named Docker volumes and database data. Do not use `down -v`,
delete volumes, replace the protected environment file, or run destructive git
commands on the VPS.

Read [VPS_DEPLOYMENT_GUIDE.md](VPS_DEPLOYMENT_GUIDE.md),
[IP_BOOTSTRAP_DEPLOYMENT_GUIDE.md](IP_BOOTSTRAP_DEPLOYMENT_GUIDE.md), and
[VPS_BACKUP_RESTORE.md](VPS_BACKUP_RESTORE.md).

## 9. Routine health checks

```bash
# On the VPS, from /root/ViralForge after exporting the production env file.
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  -f docker-compose.apache-proxy.yml --env-file .env.ip-bootstrap ps

docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  -f docker-compose.apache-proxy.yml --env-file .env.ip-bootstrap \
  exec -T worker celery -A app.worker:celery_app inspect ping --timeout=5

docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  -f docker-compose.apache-proxy.yml --env-file .env.ip-bootstrap \
  logs --tail=100 api worker scheduler discord
```

Healthy normal state: API, PostgreSQL, Redis, worker, scheduler, Discord, and
Caddy are up; the migration service has exited successfully. A queue count can
be nonzero even when `/viralforge review` has no work; check **Creative review**
in Operations to know whether an operator decision is actually pending.

## 10. Troubleshooting

| Symptom | First check |
| --- | --- |
| Discord says the interaction failed | `logs --tail=150 discord`; confirm the bot token, guild ID, role IDs, and current deployed commit |
| `/viralforge review` is empty | Open `/viralforge operations`; creative review should be zero. If not, inspect worker/API logs and the selected brand. |
| Operations says review is required but review is empty | Update to commit `c73a2e2` or later, then refresh Operations. |
| Project stops at preparing | Check worker logs, Redis health, yt-dlp, FFmpeg/FFprobe, source authorization, and project error fields. |
| Clip preview fails | Refresh the private preview link; it may have expired or reached its access limit. |
| YouTube channel discovery fails | Verify the YouTube API key is configured on the VPS without displaying it; channel validation requires a supported public channel reference. |
| Public site fails but containers are healthy | Verify Apache’s `viralforge.mxf-labs.com` proxy and the Caddy loopback listener. Do not change MxF Labs 80/443 ownership. |

## 11. Test and release checklist

Before merging or deploying application changes:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q
& .\.venv\Scripts\python.exe -m ruff check .
& .\.venv\Scripts\python.exe -m mypy app
```

For schema changes, also run Alembic upgrade/downgrade and
`scripts/schema_drift.py` against the supported database environments. Review
logs for stack traces or leaked secrets. Back up PostgreSQL before production
migrations. A successful build is not proof of provider/OAuth readiness; test
only one explicitly approved private/unlisted or draft provider flow when its
credentials are configured.

## 12. Further documentation

- Product scope and roadmap: [docs/PRODUCT_SCOPE.md](docs/PRODUCT_SCOPE.md),
  [docs/ROADMAP.md](docs/ROADMAP.md)
- Ingestion and storage: [docs/INGESTION_ARCHITECTURE.md](docs/INGESTION_ARCHITECTURE.md),
  [docs/STORAGE_ARCHITECTURE.md](docs/STORAGE_ARCHITECTURE.md),
  [docs/MEDIA_RETENTION.md](docs/MEDIA_RETENTION.md)
- Media previews/downloads: [MOBILE_CLIP_DOWNLOAD_GUIDE.md](MOBILE_CLIP_DOWNLOAD_GUIDE.md),
  [docs/MOBILE_DOWNLOAD_SECURITY.md](docs/MOBILE_DOWNLOAD_SECURITY.md)
- Publishing: [PUBLISHING_FOUNDATION_IMPLEMENTATION_REPORT.md](PUBLISHING_FOUNDATION_IMPLEMENTATION_REPORT.md),
  [MANUAL_PUBLICATION_OPERATOR_GUIDE.md](MANUAL_PUBLICATION_OPERATOR_GUIDE.md)
- TikTok: [TIKTOK_PILOT_OPERATIONS_GUIDE.md](TIKTOK_PILOT_OPERATIONS_GUIDE.md),
  [TIKTOK_RUNTIME_VERIFICATION_GUIDE.md](TIKTOK_RUNTIME_VERIFICATION_GUIDE.md)
- Operations and automation: [OPERATIONS_CENTER_OPERATOR_GUIDE.md](OPERATIONS_CENTER_OPERATOR_GUIDE.md),
  [AUTOPILOT_OPERATOR_GUIDE.md](AUTOPILOT_OPERATOR_GUIDE.md)
- Quality/corrections: [RENDERED_MEDIA_QUALITY_OPERATOR_GUIDE.md](RENDERED_MEDIA_QUALITY_OPERATOR_GUIDE.md),
  [CLIP_CORRECTION_OPERATOR_GUIDE.md](CLIP_CORRECTION_OPERATOR_GUIDE.md)
