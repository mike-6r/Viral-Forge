# Content lifecycle

`DISCOVERED → IMPORTED → SOURCE_VERIFICATION_REQUIRED → RIGHTS_REVIEW_REQUIRED → MODERATION_REQUIRED → READY_FOR_RANKING → RANKED → PROCESSING_QUEUED → PROCESSING → REVIEW_REQUIRED → APPROVED → SCHEDULED → PUBLISHING → PUBLISHED` is the normal path. Rejection, failure, blocking, and archival paths are explicit in `app/content/lifecycle.py`.

Every permitted transition writes an audit event. Invalid transitions raise a domain error. `APPROVED`, `SCHEDULED`, `PUBLISHING`, and `PUBLISHED` require manual rights and moderation approvals. `APPROVED` additionally requires a separate approved human review record.
# Manual upload placement

A successful manual upload is recorded as `DISCOVERED`, then transitions through `IMPORTED` to `SOURCE_VERIFICATION_REQUIRED`. Upload success and a claimed rights declaration never skip the existing rights or moderation gates.
