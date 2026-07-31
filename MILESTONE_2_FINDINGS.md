# Milestone 2 Implementation Findings

| Requirement | Planned implementation | Affected files | Verification | Status |
|---|---|---|---|---|
| Approved source ingestion | Domain models/services for sources, policies, jobs, provenance, feeds, and duplicate matches. | `app/sources`, `app/ingestion`, migration | Domain/API tests | Partial — manual URL vertical slice complete. |
| URL and SSRF safety | Central normalizer plus DNS-aware safe HTTP boundary; no protected-platform adapters. | `app/ingestion/url.py`, `app/ingestion/http.py` | Focused SSRF/normalization tests | Partial — local/private URL blocking and normalization complete; safe outbound client remains. |
| File ingestion | Signature validation, SHA-256, opaque local storage, and duplicate protection. | `app/ingestion/storage.py`, services | Upload tests | Planned |
| RSS/Atom | Active-source subscriptions, bounded parsing, GUID deduplication, and Celery task boundary. | `app/ingestion/feeds.py`, `app/worker.py` | Feed tests | Planned |
| Explicit schema evolution | New immutable Alembic revision; no changes to `0001`. | `alembic/versions/0002_approved_source_ingestion.py` | Upgrade/drift tests | Planned |
