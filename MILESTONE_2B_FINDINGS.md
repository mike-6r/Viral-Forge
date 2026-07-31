# Milestone 2B Findings

| Requirement | Existing support | Missing implementation / affected files | Verification | Status |
|---|---|---|---|---|
| Streaming secure uploads | Ingestion jobs and a byte-oriented storage placeholder exist. | Replaced `app/ingestion/storage.py`; added upload service and endpoint. | Chunked temporary-directory tests. | Complete |
| Signature validation | None. | Added small container-signature detector in `app/ingestion/upload.py`. | MP4/MOV/WebM/MKV and disguised-file tests. | Complete |
| Storage isolation | Local helper writes an in-memory byte value into one tree. | Added temp/final roots, opaque keys, resolved-path checks, atomic finalization, cleanup. | Storage safety tests. | Complete |
| Asset persistence | `MediaAsset` has key/type/checksum only. | Added explicit `0003_secure_media_uploads` migration with upload metadata, status, uploader/source, hash uniqueness, and job asset result. | Upgrade/downgrade/drift tests. | Complete |
| Policy/provenance/lifecycle | Existing source policy, content source, audit, lifecycle, and jobs are reusable. | Added upload-specific policy enforcement and workflow coordination. | Workflow, policy, duplicate, audit tests. | Complete |
| Rights declaration | Rights foundation exists. | Stored unapproved uploader claim in source metadata; no approval is created. | Workflow test. | Complete |
| Documentation/configuration | Existing security and ingestion docs. | Added upload/storage/media/security docs plus settings. | Documentation review and settings tests. | Complete |
