# Ingestion architecture

Milestone 2A implements one synchronous manual URL metadata-ingestion vertical slice. `app.ingestion.service` owns workflow and persistence; `app.ingestion.policy` owns source permission decisions; `app.ingestion.http` owns network safety; and `app.ingestion.metadata` owns parsing. This separation prevents routes, tasks, and parsers from bypassing outbound safety controls.

The existing source, source-policy, ingestion-job, duplicate, provenance, verification, and feed schema remains intact. Raw metadata uses the existing source `provider_metadata` JSON field, so no schema change was necessary.

RSS/Atom ingestion, uploads, Celery-driven ingestion, media download, rate limiting, clipping, publishing, and platform scraping are intentionally outside this slice.

Milestone 2B adds the upload slice: `app.ingestion.upload` coordinates policy, storage, hash duplicate detection, provenance, lifecycle, and audit while `app.ingestion.storage` is the only local filesystem boundary. RSS/Atom, Celery ingestion, processing, and publishing remain out of scope.
