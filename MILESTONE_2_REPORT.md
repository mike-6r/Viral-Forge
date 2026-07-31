# ViralForge Milestone 2 Report

## Status

**COMPLETE THROUGH 2C.** Milestones 2A and 2B provide safe manual URL metadata ingestion and secure manual media uploads; Milestone 2C adds bounded manual RSS/Atom feeds. Asynchronous scheduling remains out of scope.

## Implemented

- Immutable migration `0002_approved_source_ingestion` adds source type/status, policy fields, ingestion jobs, feed/subscription records, source verifications, and duplicate matches.
- Central URL normalization rejects non-HTTP(S), embedded credentials, localhost, private/link-local IP addresses, and tracking parameters while preserving unknown identifier parameters.
- Manual URL submission creates an idempotent ingestion job, safely fetches only bounded HTML/XHTML metadata, records provenance, detects normalized/canonical duplicates, emits audit events, and places content in `SOURCE_VERIFICATION_REQUIRED`.
- A centralized asynchronous `httpx` client validates fresh DNS results, blocks SSRF destinations, handles redirects manually, disables proxy trust/cookies/auth headers, and applies response type/size/time limits.
- Dedicated metadata extraction retains bounded raw values and deterministic selected title/description values without JavaScript, headless browsing, or media fetching.
- Manual video upload streams into opaque temporary local storage, validates MP4/MOV/WebM/MKV signatures, calculates SHA-256, deduplicates exact bytes without merging provenance, and remains verification-required.
- API routes: `POST /api/v1/ingestion/url`, `GET /api/v1/ingestion/jobs`, `GET /api/v1/ingestion/jobs/{job_id}`, `POST /api/v1/sources`, and `POST /api/v1/sources/{source_id}/activate`.
- Feed routes support registration, validation, bounded manual execution, operational state, entries, and run history. See `docs/FEED_INGESTION.md`.

## Verification

- pytest: 56 passed.
- Ruff: passed.
- mypy: passed.
- Alembic upgrade and schema drift: passed on SQLite.

## Remaining work

Feed parser/polling/routes, actual ingestion Celery tasks, rate limiting, and media processing remain to be implemented. No restricted platform scraping or media processing was added.
