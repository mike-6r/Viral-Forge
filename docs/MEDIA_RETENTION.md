# Media retention and cleanup

`MediaAsset` retains immutable project, audit, publishing, and analytics history after a local object is deleted. The cleanup task only receives opaque storage keys and deletes through `LocalFilesystemStorage`.

Defaults are configurable: temporary files 6h, proxies/rejected/published clips 24h, unreviewed/approved clips 72h, sources 48h, and unresolved failures 7d. Eligibility always also requires a retention deadline and no administrative hold, active preview grant, unresolved review, scheduled/uploading publish request, retry, or required source dependency. It is not age-only deletion.

Run `viralforge.cleanup_expired_media` from Celery Beat or a bounded external scheduler; it is safe to run hourly and supports dry-run. Storage summary intentionally returns capacity and aggregate byte counts, never storage paths. Emergency cleanup is limited to already eligible objects.
