# ViralForge Foundation Milestone 1 Audit Report

## Overall result

**PASS WITH CONDITIONS.** The critical unsafe approval and default-admin behaviors found during this audit were corrected and regression-tested. The repository is not ready for production and should not begin Milestone 2 until the remaining architecture and migration-history conditions are accepted or resolved.

## Scope and inspected areas

The audit independently inspected the full repository tree; all application modules; tests; configuration; dependency and container files; all documentation and ADRs; Alembic environment and migrations; lifecycle and authorization paths; and safe worker task definitions. Claims in `MILESTONE_1_REPORT.md` were not used as evidence.

## Findings

The authoritative finding register is `MILESTONE_1_AUDIT_FINDINGS.md`.

- **High, corrected:** rights records marked `DENIED`, `DISPUTED`, `RESTRICTED`, `UNKNOWN`, or expired could satisfy the old approval query when paired with `APPROVED` disposition.
- **High, corrected:** moderation rejection could be ignored when any separate approval existed.
- **High, corrected:** arbitrary development header UUIDs were granted `ADMIN` and were not required to identify persisted users, which would violate PostgreSQL audit foreign keys.
- **High, open:** `0001_foundation` remains a mutable-model migration (`Base.metadata.create_all`), preventing a reproducible historical schema snapshot.
- **Medium, corrected/partial:** optimistic content versioning, request/correlation log context, URL-query redaction, validation error envelope, job fields, and basic bounds were added.
- **Medium, open:** HTTP route handlers still own some persistence and transaction orchestration; logging does not yet bind all requested entity/actor/job/service/environment fields.

## Corrections and tests

Changed files include lifecycle, auth, content/ranking models, API, logging, tests, the corrective migration `0002_harden_foundation`, and this audit documentation.

Six new regression tests cover ineligible rights states, expiry, moderation rejection, and SYSTEM review denial. Existing API tests now use a persisted development actor with a real role. The suite has **16 passing tests**.

## Verification

- `pytest`: **16 passed**; one upstream FastAPI/TestClient deprecation warning.
- `ruff check .`: **passed**.
- `mypy app`: **Success: no issues found in 32 source files**.
- Migration: fresh SQLite `upgrade head`, `downgrade base`, and re-upgrade completed at `0002_harden_foundation`.
- Live API: started against a migrated SQLite database with a persisted `ADMIN` actor. `/health` returned OK; creation and `DISCOVERED → IMPORTED` worked; illegal publish transition returned 409; malformed UUID returned 422; missing actor returned 401.
- Worker: safe task functions were executed directly and returned their documented safe responses. Docker CLI and Redis were unavailable, so no Docker/Redis-backed worker was verified.
- Secret scan: no private-key, common cloud/API token, bearer-token, or password-assignment signatures were found outside intentional placeholders.

## Remaining limitations and conditions

1. Replace the mutable initial migration with explicit immutable DDL before release or any shared environment. The existing `0002` correction supports upgrades but does not erase this historical risk.
2. Move route-level create/transition persistence orchestration into application services before expanding the API.
3. Define score-component scales and add their PostgreSQL bounds.
4. Extend structured-log context to bind service, environment, actor, entity, and job fields at actual event emission.
5. Verify PostgreSQL, Redis, Docker Compose, and a real Celery worker in an environment where those services are available.

## Manual verification commands

```powershell
Copy-Item .env.example .env
docker compose up --build -d
docker compose run --rm api alembic upgrade head
docker compose exec api python -m pytest
docker compose exec api python -m ruff check .
docker compose exec api python -m mypy app
docker compose logs api worker
docker compose down
```

## Milestone 2 recommendation

Do **not** begin Milestone 2 until the explicit-migration condition is resolved. After that, the recommended next scope remains approved-source ingestion only; it must not add scraping, downloading, media processing, Discord, publishing, analytics collection, or automation.
