# ViralForge Milestone 2A Report

## Result

**COMPLETE.** This delivery implements the requested safe metadata retrieval and complete manual URL-ingestion slice. No Milestone 3 work, uploads, feeds, Celery ingestion tasks, rate limiting, media download, clipping, publishing, or platform scraping was added.

## Implemented

- `SafeOutboundHttpClient` is the central async `httpx` network boundary. It requires HTTP(S), verifies TLS, disables proxy environment trust, does not send cookies/auth headers, applies connect/read/write/pool and total timeouts, closes connections deterministically, and has a controlled user agent.
- DNS is resolved and every answer is validated before the initial request and every redirect. Local/private/loopback/link-local/multicast/unspecified/reserved/CGNAT destinations, local aliases, metadata aliases, and mixed public/private answers are rejected.
- Redirects are manual and bounded. Relative locations are normalized and rechecked for URL safety, DNS safety, and source policy. The high-level transport cannot connection-pin a prevalidated address; the documented DNS TOCTOU limitation remains.
- Responses are streamed, reject unsupported content before body consumption, and enforce declared and actual byte limits. Only HTML/XHTML is parsed; binary/video bodies are not downloaded.
- A separate Beautiful Soup parser extracts bounded raw metadata and deterministic selected title/description values. Canonical and Open Graph/Twitter media URLs are normalized as unverified metadata only and are never fetched.
- Manual ingestion now persists its job before untrusted I/O, applies policy/duplicate checks, records safe failures as `FAILED` or bounded `RETRY_SCHEDULED`, avoids partial content on failure, writes audit events, creates provenance on success, and places content in `SOURCE_VERIFICATION_REQUIRED`.
- `POST /api/v1/ingestion/url` supports optional source ID, idempotency key, and notes; it returns normalized/final URL, selected metadata, lifecycle state, warnings, and correlation ID.

## Schema

No migration was required. The existing `Source.provider_metadata` JSON field stores raw and selected retrieval metadata, while existing ingestion-job, provenance, duplicate, audit, and lifecycle structures supply the required operational records. Revisions `0001` and `0002` were not edited.

## Verification

- Dependency synchronization: editable install with `beautifulsoup4` and runtime `httpx` completed.
- Pytest: **40 passed**. Tests are fully controlled and do not contact the public internet. They cover safe/public-style DNS injection, local/private/mixed DNS rejection, redirects, type/size limits, metadata priority/limits, success, canonical duplicate, idempotency, failed-job persistence, retry classification, source policy, lifecycle, audit, and missing actor behavior.
- Ruff: **passed**.
- mypy: **Success: no issues found in 41 source files**.
- Migration: SQLite upgrade to head, downgrade to base, and re-upgrade passed.
- Schema drift: **no drift detected** before and after the downgrade/re-upgrade cycle.
- API: application creation and `/health`/`/ready` execution are covered by the FastAPI TestClient suite. A foreground Uvicorn launch remained running until the local verification command deadline, consistent with a started server; no public-network request was made.
- Log review: test output contains only local test/HTTP access diagnostics and no secrets, remote response bodies, stack traces, or restart loops.

## Files changed

- Application: `app/api.py`, `app/common/config.py`, `app/ingestion/http.py`, `app/ingestion/metadata.py`, `app/ingestion/policy.py`, `app/ingestion/service.py`.
- Dependencies/config: `pyproject.toml`, `.env.example`.
- Tests: `tests/test_api.py`, `tests/test_ingestion.py`, `tests/test_safe_metadata_ingestion.py`.
- Documentation: `README.md`, `docs/SAFE_HTTP_CLIENT.md`, `docs/METADATA_EXTRACTION.md`, `docs/MANUAL_URL_INGESTION.md`, `docs/INGESTION_ARCHITECTURE.md`, `docs/URL_NORMALIZATION.md`, `docs/NETWORK_SAFETY.md`, `docs/SECURITY.md`, `MILESTONE_2_REPORT.md`, and `MILESTONE_2A_FINDINGS.md`.

## Known limitations and next slice

JavaScript-rendered metadata, headless browsing, media download, and automatic canonical/media retrieval are intentionally unsupported. DNS preflight validation cannot eliminate the connection-time DNS-rebinding TOCTOU window without a pinned-address transport. Public accessibility and metadata never authorize reuse.

The next recommended slice is manual uploads with opaque storage, file signature/size validation, checksums, and duplicate handling. Do not combine it with feeds or asynchronous ingestion.
