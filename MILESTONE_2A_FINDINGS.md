# Milestone 2A Findings

| Requirement | Existing gap / implementation location | Verification | Status |
|---|---|---|---|
| Central outbound HTTP boundary | Manual ingestion created content from the submitted URL without making a request. Added `app/ingestion/http.py`. | Controlled `httpx` transport tests. | Complete |
| SSRF and redirects | `url.py` rejected unsafe literals but did not resolve DNS or revalidate redirects. | Resolver tests for local, private, mixed, metadata, relative redirect, loop, and redirect-limit destinations. | Complete |
| Bounded metadata retrieval | No response type, byte limit, timeout, or content streaming existed. | Declared/actual size, binary/video, and response-type tests. | Complete |
| Metadata extraction | No parser or stored fetched-page metadata existed. Added `app/ingestion/metadata.py`; persist bounded raw metadata on `Source.provider_metadata`. | Parser priority, malformed input, canonical, duplicate-tag, encoding, and length-limit tests. | Complete |
| Complete URL workflow | `submit_url` marked a job successful and created content before fetching; it did not record safe failure categories or lifecycle placement. Updated `app/ingestion/service.py`. | Success, duplicate, failure-persistence, lifecycle, policy, retry, and audit tests. | Complete |
| API contract | The endpoint required an idempotency key and returned only the minimal job representation. Updated `app/api.py` with optional source/key/notes and retrieval summary. | FastAPI actor/authentication and existing API suite. | Complete |
| Schema | Existing `sources.provider_metadata`, `ingestion_jobs`, provenance, audit, and duplicate tables are sufficient. | Existing upgrade and drift suite. | Complete — no migration needed |
| Documentation/configuration | No safe HTTP or metadata documents/configuration existed. | Documentation review and settings tests. | Complete |
