# Feed operations

`PENDING_VALIDATION` feeds are awaiting validation; successful validation becomes `ACTIVE`. Active feeds can be manually run. A retryable retrieval or parse failure becomes `FAILING`, while `PAUSED` requires activation and `BLOCKED`, `REJECTED`, and `ARCHIVED` cannot run. Blocking requires an administrative reason and safely releases any stored lease.

Runs retain ETag and Last-Modified validators. A conditional `304 Not Modified` is a successful checked run, updates eligibility, and does not parse or import entries. The persisted lease rejects concurrent runs. The next eligible time uses the largest of feed polling, source-policy minimum interval, and application minimum. API responses never return raw XML or remote response bodies.

Owners and administrators register, validate, update, pause, and activate feeds; only administrators block them. Editors may run an active feed but only owners/admins may use bounded operational overrides. Feed PATCH requires the returned `version_id`; a stale version returns 409.
