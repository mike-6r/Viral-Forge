# Intelligent Clip Opportunity Engine Implementation Report

## Architecture

The opportunity engine is a dedicated domain between completed AI analysis and clip rendering. It consumes only persisted `VideoAnalysis`, `AnalysisSegment`, `TranscriptSegment`, and `AnalysisEvent` records. It never opens the source video or invokes the analysis layer.

The prior fixed-window clipping route remains available and unchanged for compatibility. The new opportunity route creates a ranked, human-reviewable list. Approving an opportunity renders exactly one pending clip through the existing FFmpeg command path; it does not approve the clip for posting or create a posting-queue item.

## Scoring and ranking

`RuleOpportunityProvider` is the configured default behind an `OpportunityProvider` protocol. It creates padded natural windows around stored speech, scenes, motion, audio, transcript, and event signals, merges windows above the configured overlap tolerance, and scores the remaining windows with normalized configurable weights.

Stored reason rows make every score explainable. The default components are speech quality, motion, scene change, transcript confidence, OCR activity, audio energy, silence context, interesting event count, and visual activity. Configuration rejects a score profile where one signal exceeds 40% of the total weight.

## Persistence and stale review protection

Migration `0013_clip_opportunities` adds:

- `opportunity_generation_runs` for queued/running/completed/failed detection work.
- `clip_opportunities` for ranked windows, review state, render state, overlap, and generated clip linkage.
- `opportunity_reasons` for every weighted component.
- `clip_opportunity_versions` for optimistic-locking review history and stale-review prevention.

An existing completed run is returned without regenerating. Explicit regeneration creates a new generation version and marks older pending opportunities stale. Review actions require the expected version; duplicate approval safely returns the same opportunity and generated clip.

## Worker flow

`viralforge.generate_clip_opportunities` is registered on the existing Celery worker. It reads an already-completed analysis, generates ranked opportunities through the selected provider, stores reasons and audit events, and records success or failure on the generation run. It does not download, analyze, publish, or enqueue posting work.

## API

New authorized routes:

- `POST /api/v1/production/projects/{project_id}/opportunities`
- `POST /api/v1/production/projects/{project_id}/opportunities/regenerate`
- `GET /api/v1/production/projects/{project_id}/opportunity-generation`
- `GET /api/v1/production/projects/{project_id}/opportunities`
- `GET /api/v1/opportunities/{opportunity_id}`
- `GET /api/v1/opportunities/{opportunity_id}/reasons`
- `GET /api/v1/opportunities/{opportunity_id}/versions`
- `POST /api/v1/opportunities/{opportunity_id}/approve`
- `POST /api/v1/opportunities/{opportunity_id}/reject`

Approval invokes the existing rendering command for only the approved window. It leaves the resulting `ProductionClip` pending, so publication remains a separate human decision.

## Discord

The existing project dashboard now shows ranked/approved opportunity counts. It has compact controls to detect and browse opportunities. The review view has Previous, Next, Approve, Reject, and View Details controls, and displays timing, score, confidence, top reasons, explanation, and transcript preview without creating bulk messages.

## Configuration

`Settings` and `.env.example` now expose enablement, provider ID, minimum score, maximum results, minimum/maximum duration, padding, merge overlap, fallback behavior, and all component weights. Defaults use the local rule provider with a 35-point minimum score.

## Files changed

- `app/opportunities/__init__.py`
- `app/opportunities/models.py`
- `app/opportunities/service.py`
- `alembic/versions/0013_intelligent_clip_opportunities.py`
- `app/common/config.py`
- `.env.example`
- `app/production/service.py`
- `app/api.py`
- `app/worker.py`
- `app/discord_bot.py`
- `scripts/schema_drift.py`
- `tests/test_opportunities.py`
- `tests/test_discord_bot.py`
- `tests/test_worker_foundation.py`

## Testing and verification

- Unit/API/worker/Discord/migration tests pass.
- Ruff, mypy, and SQLite schema-drift checks pass.
- Docker images rebuilt and PostgreSQL migrated to `0013_clip_opportunities`.
- API readiness, Celery ping, worker task registration, and Discord Gateway connection pass.
- A local generated video with persisted mocked analysis signals produced one worker-ranked opportunity with a 48.77 score and eight stored reasons.
- Approving it rendered one successful clip. A repeated approval returned the same clip, and the posting queue remained empty.

## Next recommended milestone

Do not implement it here. The next milestone should generate AI titles, captions, hashtags, descriptions, platform-specific metadata, and thumbnail suggestions for approved clips.
