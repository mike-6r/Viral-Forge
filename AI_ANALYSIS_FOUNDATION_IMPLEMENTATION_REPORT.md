# AI Analysis Foundation Implementation Report

## Architecture

Analysis is a separate, persisted domain at the boundary after a source has been downloaded. It has no call path into fixed-length clip generation, clip approval, posting, discovery, or source-quality logic.

`ProductionProject` remains unchanged as the source/workflow record. `VideoAnalysis` is the durable analysis record associated with one project and source version. A unique project/version constraint and idempotent service methods ensure that an existing `foundation-v1` analysis is returned rather than run again. A re-run is an explicit, authorized action and is rejected while that analysis is already running.

## Models and migration

Migration `0012_ai_analysis_foundation` adds these PostgreSQL tables:

- `video_analyses` for lifecycle state, technical metadata, provider metadata, and analysis version.
- `analysis_segments` for timestamped timeline facts such as scenes, speech, motion, or audio regions.
- `transcript_segments` for timestamped text, optional speaker, and confidence.
- `analysis_events` for flexible, schema-free event types such as shot changes and detected text.

The migration was applied to the local PostgreSQL database and was included in the SQLite schema-drift and upgrade/downgrade/re-upgrade verification.

## Providers

`app.analysis.service` defines swappable protocols for video analysis, transcription, OCR, future vision, object detection, and future summarization. The configured default video provider uses `ffprobe` to persist duration, resolution, frame rate, bitrate, codec, and audio channels. It creates a neutral sampling/timeline record and explicitly identifies scene/audio/motion detection as provider placeholders.

The default transcript and OCR providers are safe mocks. They return no data instead of failing a job. Provider failures are recorded as non-secret metadata and structured warnings; technical-analysis failures mark the job as failed.

No clip selection, ranking, score, publication, discovery behavior, source-quality behavior, or clipping algorithm was added or changed.

## Worker flow

`viralforge.run_video_analysis` is registered on the existing Celery worker. It finds the persisted record, safely ignores an already running/completed/cancelled job, performs the analysis, and records audit events for queued, started, completed, failed, and cancelled transitions. The configured analysis concurrency is applied to the worker configuration.

An actual local worker-delivered verification job processed a generated one-second video successfully. A repeat request returned the same completed analysis rather than creating a duplicate.

## API

Authorized routes added:

- `POST /api/v1/production/projects/{project_id}/analysis`
- `POST /api/v1/production/projects/{project_id}/analysis/rerun`
- `GET /api/v1/production/projects/{project_id}/analysis`
- `GET /api/v1/analysis/{analysis_id}`
- `GET /api/v1/analysis/{analysis_id}/timeline`
- `GET /api/v1/analysis/{analysis_id}/transcript`
- `GET /api/v1/analysis/{analysis_id}/events`
- `POST /api/v1/analysis/{analysis_id}/cancel`

Analysis can only be requested after a downloaded source exists. New/re-run requests queue the Celery task outside tests. Reads reuse existing role authorization patterns.

## Discord additions

The existing compact project dashboard now includes analysis status, technical duration/resolution/frame rate when available, transcript availability, timeline count, scene count, speech duration, and event count. It adds fields to the existing dashboard message and does not create additional review messages.

## Configuration

`.env.example` and `Settings` now define analysis enablement, max concurrency, video/transcript/OCR provider identifiers, frame-sampling interval, scene threshold, timeout, and maximum source duration. Defaults are local and safe: `ffprobe` plus mock transcription/OCR.

## Files changed

- `app/analysis/__init__.py`
- `app/analysis/models.py`
- `app/analysis/service.py`
- `alembic/versions/0012_ai_analysis_foundation.py`
- `app/common/config.py`
- `.env.example`
- `app/api.py`
- `app/worker.py`
- `app/discord_bot.py`
- `scripts/schema_drift.py`
- `tests/test_analysis.py`
- `tests/test_discord_bot.py`
- `tests/test_worker_foundation.py`

## Verification

- `python -m pytest -q`: passed.
- `python -m ruff check .`: passed.
- `python -m mypy app`: passed.
- `python scripts/schema_drift.py`: passed with no drift.
- Docker build: passed.
- Local PostgreSQL migration: passed to `0012_ai_analysis_foundation`.
- API `/health` and `/ready`: passed.
- PostgreSQL, Redis, Celery inspect ping, worker task registration, and Discord Gateway connection: passed.
- A mocked container-level analysis completed with three timeline segments, one transcript segment, and one event.
- A worker-delivered local-video analysis completed with no duplicate on repeated request.

## Exact next milestone

The next milestone may consume these stored analysis records to identify and rank human-reviewable clipping opportunities instead of relying solely on fixed-length segments. It must remain a separate implementation and is not included here.
