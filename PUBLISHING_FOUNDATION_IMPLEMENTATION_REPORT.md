# Publishing Foundation Implementation Report

## Outcome

The review-first publishing foundation is implemented with the official YouTube Data API v3 resumable-upload boundary as the only publishing provider. Publishing is disabled by default, and the provider refuses `public` privacy settings even when enabled.

## Controls implemented

- Explicit `PublishRequest` lifecycle: `AWAITING_CONFIRMATION` -> `QUEUED` or `SCHEDULED` -> `UPLOADING` -> `SUCCEEDED` / `FAILED` / `CANCELLED`.
- No approved clip, content package, queue record, worker tick, or Discord view can silently create an upload. A privileged human must create a manual or scheduled request and separately confirm it.
- Required gates: accepted source audit event, successfully rendered and approved clip, approved content package for that clip, active YouTube destination account in the same brand, explicit moderation approval, and rights approval when the per-clip gate marks rights as required.
- `PublishReviewGate` stores the publishing-specific rights/moderation disposition. Existing generic rights and moderation records are not repurposed because they target a different content domain.
- `DestinationAccount` continues to store only `credential_reference_id`; the provider accepts only an opaque `env://NAME` reference and resolves the secret at runtime. Tokens are neither persisted nor included in audit payloads, responses, or logs.
- `PublishingAccountConnection` persists only safe connection state and channel identity/error category. Connection verification uses the official YouTube `channels.list(mine=true)` endpoint.
- YouTube metadata is chosen from the approved content package: Shorts title, description/attribution, and hashtags. The request snapshots selected metadata and forces `unlisted` by default.
- FFprobe validates an existing rendered MP4, video stream, positive duration, and a maximum 180-second duration before an upload begins.
- Idempotency key uniqueness prevents duplicate publish requests; retries use persisted attempt rows, safe failure categories, exponential backoff, and `next_attempt_at`.
- Upload request status and percentage are persisted (`5`, `25`, `100` milestones), along with remote YouTube ID and URL on success.
- Cancellation is allowed only before `UPLOADING`; it is recorded as an audit event and cannot result in a later upload.
- API endpoints cover connection verification, review-gate decisions, request creation, explicit confirmation, cancellation, history, and attempt history. Discord adds `/viralforge publish <request_id>`, presenting an explicit Confirm/Cancel interaction.
- The Celery worker has one task for a confirmed request and one bounded due-request/retry tick. Neither task acts on an unconfirmed request.

## Files changed

- `app/publishing/models.py`
- `app/publishing/service.py`
- `app/publishing/__init__.py`
- `app/api.py`
- `app/worker.py`
- `app/discord_bot.py`
- `app/common/config.py`
- `alembic/versions/0017_publishing_foundation.py`
- `alembic/env.py`
- `.env.example`
- `tests/conftest.py`
- `tests/test_publishing_foundation.py`

## Verification

- Focused publishing tests: 3 passed.
- Full suite: 96 passed.
- Ruff: passed.
- mypy: passed.
- Docker API and worker images rebuilt; API `/health` returned `200` and PostgreSQL/Redis/API were healthy.
- Worker connected to Redis, answered `inspect ping`, and registered `viralforge.execute_publish_request` plus `viralforge.execute_due_publish_requests`.
- PostgreSQL migration `0017_publishing_foundation` upgraded, downgraded to `0016_multi_brand_foundation`, and upgraded again. The database is at revision `0017_publishing_foundation`, and all four publishing tables exist.

## YouTube live-upload status

No live YouTube upload was attempted. The current runtime reports both `VIRALFORGE_PUBLISHING_ENABLED=false` and `VIRALFORGE_PUBLISHING_YOUTUBE_ENABLED=false`; therefore a private/unlisted test would be intentionally blocked. Automated tests use no OAuth credentials and cannot issue a public upload.

To conduct the single allowed live verification later, an authorized operator must enable both flags, create an active same-brand YouTube destination account whose `credential_reference_id` is an external `env://...` reference to a valid OAuth access token with YouTube upload scope, approve every required gate, create and confirm a request, and leave privacy at `unlisted` or `private`.

## Remaining issue

`alembic check` still reports pre-existing repository drift unrelated to the publishing tables: legacy multi-brand indexes/nullability and older feed/media type metadata. The publishing models are now imported by Alembic, so this result is no longer hidden. It does not prevent the tested `0017` upgrade/downgrade cycle or the running publishing foundation, but it should be reconciled in a separately scoped migration-hardening task rather than silently suppressed here.
