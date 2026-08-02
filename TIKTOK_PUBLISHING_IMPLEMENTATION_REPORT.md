# TikTok publishing implementation report

## Delivered

- Official TikTok Login Kit / Content Posting API v2 adapter with separate draft and Direct Post modes.
- Brand-scoped destination validation, hashed expiring OAuth state, safe creator-capability snapshots, and no token/upload-URL database persistence.
- Explicit request lifecycle, idempotency boundary, draft operator-completion state, bounded transfer/status tasks, and manual analytics handoff.
- Production configuration validation: no HTTP callback, no IP-bootstrap OAuth, no weak OAuth state secret, and no unaudited public Direct Post.
- Mocked automated tests; no TikTok HTTP call or public upload is made by tests.

## Completion update

The follow-up hardening pass adds an encrypted, atomic VPS credential-store backend; PKCE verifier storage; OAuth credential creation, replacement, refresh, revocation, and deletion; token-expiry metadata; and a forward-only `0026_tiktok_credential_lifecycle` migration. Environment references remain intentionally read-only.

The provider's mocked FILE_UPLOAD contract now streams sequentially with TikTok-valid chunk sizing, records byte progress, never persists the upload URL, and treats final-transfer uncertainty as `UNKNOWN_REMOTE_OUTCOME` rather than blindly retrying.

See [TIKTOK_PROVIDER_COMPLETION_REPORT.md](TIKTOK_PROVIDER_COMPLETION_REPORT.md) for the audited readiness table, [CREDENTIAL_STORE_OPERATIONS_GUIDE.md](CREDENTIAL_STORE_OPERATIONS_GUIDE.md) for secret-store operations, and [TIKTOK_RUNTIME_VERIFICATION_GUIDE.md](TIKTOK_RUNTIME_VERIFICATION_GUIDE.md) for the VPS-only commands.

## Real verification status

No real TikTok transfer was attempted. Required external prerequisites remain: trusted HTTPS hostname, TikTok developer app with Login Kit and Content Posting API configured, registered exact redirect URI, approved requested scopes, and an authorized test creator. After the production-container checks pass, run exactly one draft or `SELF_ONLY` Direct Post only with explicit operator approval.

## Local verification

- `pytest -q --ignore=tests/test_analysis.py`: passed (136 tests).
- Focused TikTok/migration/publishing tests: passed.
- Ruff: passed.
- mypy: passed.
- Disposable SQLite base-to-head, downgrade to `0024_download_progress`, re-upgrade, and schema-drift check: passed.
- `pytest -q` has one environment-only failure: this Windows host has no discoverable `ffmpeg`, so the existing real-media fixture cannot create its test video. No TikTok test makes a network request.
- Docker/PostgreSQL verification was not possible locally because Docker is not installed on this host. Run the supplied VPS Compose verification after deployment.
