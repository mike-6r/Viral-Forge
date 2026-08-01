# Deployed MVP Smoke-Test Report

Date: 2026-08-01  
Target: deployed VPS, IP-bootstrap profile  
Scope: controlled synthetic-media end-to-end verification only; no public upload or publishing action.

## Service status

| Service | Result |
| --- | --- |
| API | Healthy; `/health` returned `{"status":"ok"}` and `/ready` returned `{"status":"ready"}` through Caddy. |
| PostgreSQL | Healthy; migrations are at the single head `0021_media_preview`. |
| Redis | Healthy. |
| Celery worker | Healthy after a restart; `inspect ping` returned `pong`. All expected task names were registered. |
| Scheduler | Running. |
| Discord | Running and connected to the Discord Gateway. |
| Caddy | Running on the temporary IP-bootstrap port `8081`; the API remains private behind it. |

`alembic check` reported no upgrade operations. A disposable SQLite base-to-head migration run reported no schema drift.

## Brand and configuration status

Created the audited operational bootstrap records required for a new deployment:

- Owner/operator account: `VPS Operator` (credentials are not stored in this report).
- Brand: `BodycamsDailyHQ` in the existing legacy workspace.
- Content profile: public-safety niche, 15–60 second bounds, neutral/factual language, manual review, attribution, rights, moderation, and human-review requirements.
- Review policy: one review; source, rights, moderation, and attribution requirements enabled.
- Posting policy: maximum three posts/day, no target platforms, and public publishing disabled.

The deployed analysis configuration uses `faster_whisper`, model `tiny`, CPU, and `int8`. FFmpeg, FFprobe, and yt-dlp were detected in the worker environment.

## Controlled media pipeline

A single authorized synthetic 60-second MP4 was attached to the existing controlled smoke-test project. No external video was downloaded.

| Stage | Result |
| --- | --- |
| Source acceptance and attachment | Passed; project reached `SOURCE_READY`. |
| Analysis | Passed; `COMPLETED`, one persisted transcript segment, 66 analysis segments, and four analysis events. |
| Opportunity generation | Passed; one persisted, explainable opportunity. |
| Opportunity approval and render | Passed; exactly one clip rendered successfully. |
| Content package | Passed; local-template package generated with 17 editable fields and approved. |
| Clip review/queue | Passed; clip approved and exactly one `READY_TO_POST` queue record exists. |
| Publish activity | None. There are zero `PublishRequest` rows for the test clip. |

The source asset is 5,350,188 bytes; the rendered clip is 12,754,184 bytes. The local Whisper model cache is approximately 75 MB. Stage timings were not instrumented by the application; no fabricated timing values are reported.

## Preview and retention

- Private preview page: HTTP 200 through Caddy.
- Token-authenticated media range request: HTTP 206 with exactly 1,024 bytes returned.
- Invalid preview token: HTTP 404.
- No preview token was logged or included in this report.
- Cleanup dry-run selected and deleted zero active records.
- A deliberately expired, 46-byte disposable preview proxy was then cleaned up for real: the object was removed, its `MediaAsset` history remained with lifecycle state `DELETED`, and the approved rendered clip remained intact.

## Recovery and idempotency

- Worker restart completed successfully; the worker rejoined and answered Celery ping.
- Repeat analysis request: reused the existing completed analysis.
- Repeat opportunity-generation request: reused the one completed run/opportunity.
- Repeat content-package request: reused the approved package.
- Repeat approved-opportunity render request: returned the existing clip; total clips remained one.
- Repeat queue action: queue-row count remained one.

## Publishing readiness

The test project has an accepted source, approved clip, and approved content package. It is intentionally not publishable:

- Rights and moderation review gates are not recorded.
- No explicit destination account is configured.
- `publishing_enabled` and YouTube publishing are disabled in the deployed IP-bootstrap profile.
- No provider connection is configured.
- No human publish/schedule decision has been made.

Therefore no public upload was attempted or possible.

## Discord verification

The bot connected to the Gateway after startup, which occurs after persistent views are registered and the configured guild/global command tree is synced. Direct interactive Discord testing remains an operator action, because it requires a real Discord member interaction. In the configured guild, run:

```
/viralforge home
/viralforge brands
/viralforge project
```

Expected result: an ephemeral ViralForge control center, the `BodycamsDailyHQ` selector, and the controlled project dashboard. This is not a deployment blocker; the bot process is connected and has no current restart loop or exception.

## Failures found and exact fixes

1. API, worker, and Discord containers were restricted to an internal-only Docker network, preventing DNS/HTTPS egress for Discord and approved external providers. Added the non-published `egress` network only to those outbound clients. PostgreSQL, Redis, scheduler, and migration jobs remain on the private network.
2. The production-only media path did not register the legacy `sources` table before flushing `MediaAsset.source_id`. Registered the target in `app/media_preview/service.py`.
3. Opportunity and content-package audit writes could miss the `brands` target outside the API import order. Registered the target with `app/audit/models.py`.
4. Retention cleanup could miss the optional `MediaAsset.uploader_id` target. Registered the `users` target in `app/media_preview/service.py`.
5. The test suite inherited the deployment trusted-host list. The test fixture now explicitly uses `testserver`.
6. Added isolated model-registration regression tests.

No migration was required and no released migration was modified.

## Quality and log review

- `python -m pytest -q`: passed (warnings only from upstream FastAPI TestClient and Discord audio deprecations).
- `python -m ruff check .`: passed.
- `python -m mypy app`: passed (`71 source files`).
- `alembic check`: passed.
- `python scripts/schema_drift.py <disposable SQLite path>`: passed.
- Recent API, worker, scheduler, Discord, and Caddy logs had zero counted stack-trace/restart/connection-failure alerts and zero credential-pattern matches.

Temporary local/VPS build artifacts created by disposable quality containers were removed. The pre-existing VPS deployment script and environment files were not changed by this verification.

## Remaining external blockers and recommendation

There is no external blocker to operating the verified private review workflow. The IP-bootstrap endpoint remains intentionally HTTP-only; configure the existing HTTPS reverse proxy for `viralforge.mxf-labs.com` before sharing previews beyond the restricted operator path. The next development work should be an operator acceptance pass in Discord and hostname/TLS cutover, not another product feature.

VIRALFORGE DEPLOYED MVP TEST PASSED
