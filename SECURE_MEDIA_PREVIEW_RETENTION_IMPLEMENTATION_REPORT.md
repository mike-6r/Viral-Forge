# Secure Media Preview and Retention System

## Architecture and migration

Migration `0021_media_preview` extends the existing `media_assets` inventory and adds `preview_grants`. Existing uploads are backfilled to the legacy brand; rendered clips receive durable linked asset rows without changing clips, projects, publishing, analytics, or source keys. The migration is forward-only, has a deterministic rollback of data introduced solely by this feature, and produces one head.

## Security and streaming

Preview tokens are CSPRNG generated, HMAC-SHA256 hashed with a configured secret, and returned only at creation/explicit refresh. Browser pages and media endpoints validate expiry, revocation, limits, asset/clip/project/brand linkage, and deletion state. The player is no-cache/noindex with restrictive CSP, no referrer, nosniff, and frame denial headers. Media uses provider-only opaque keys and streaming single-byte ranges; it never buffers a complete video or exposes local paths.

## Proxy, retention, and operations

Optional FFmpeg review proxies use H.264/AAC, capped dimensions, fast-start, profile reuse, size/timeout limits, and safe fallback to the authoritative rendered clip. `viralforge.cleanup_expired_media` is bounded, dry-run capable, lock protected, idempotent, retains rows/audit history, and records reclaimed bytes. Administrative APIs cover links, retention, holds, proxy creation, dry-run cleanup, and safe aggregate storage metrics. Discord review adds an Open Preview link when freshly created and an explicit Refresh Preview Link action; it no longer relies on full MP4 uploads.

## Deployment and next milestone

See `docs/PREVIEW_DEPLOYMENT_CADDY.md`, `docs/PREVIEW_DEPLOYMENT_NGINX.md`, and `docs/MEDIA_RETENTION.md`. The exact next milestone is a production VPS deployment profile with HTTPS, persistent volumes, backups, and secure OAuth callbacks. It is not implemented here.

## Verification

`0021_media_preview` is the sole Alembic head and was applied to local PostgreSQL. Clean SQLite base-to-head, downgrade, and re-upgrade tests pass. `alembic check` and `scripts/schema_drift.py` report no drift. The complete test suite passes (103 tests), as do Ruff and mypy.

Docker images were rebuilt; PostgreSQL, Redis, API, worker, and Discord bot were restarted. `/health` and `/ready` return 200, Celery ping succeeds, and the cleanup/proxy tasks are registered. A retained local rendered clip was verified through the actual browser page and both ordinary and suffix byte ranges (`206`, `Content-Range`, `Accept-Ranges`). Invalid tokens return 404. Access logging redacts preview-token query values.

The local development preview URL is HTTP by design. Production configuration rejects HTTP and requires a strong non-default preview secret. No publishing action was requested or performed.
