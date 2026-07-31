# Media assets

Each finalized file has a `MediaAsset` record with its provider-relative key, detected and declared media types, container, byte size, lowercase SHA-256, uploader, source, correlation ID, and `VERIFICATION_REQUIRED` status. It is not available for release merely because it was stored.

The SHA-256 uniqueness strategy deduplicates identical bytes only. It does not identify re-encodes, crops, watermarks, changed audio, or visually similar media. Different submissions can share one physical asset while retaining separate source provenance and rights-review context.
