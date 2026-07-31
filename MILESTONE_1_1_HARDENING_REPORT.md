# ViralForge Milestone 1.1 — Foundation Release Hardening

## Result

**PASS WITH CONDITIONS.** The migration-release blocker is resolved. Docker, PostgreSQL, Redis, container API, and live Celery-worker verification remain unavailable because Docker is not installed in this environment.

## Migration correction

The unreleased `0001_foundation` migration previously imported live application models and ran `Base.metadata.create_all/drop_all`. It has been replaced in place, retaining revision identity, with self-contained explicit Alembic operations: stable PostgreSQL enum types, explicit tables, foreign keys, constraints, indexes, and downgrade ordering. It contains no application model imports or metadata schema generation.

The unreleased `0002_harden_foundation` migration was removed because its changes are included in the corrected initial baseline. This revision-history modification occurred before shared deployment; the repository has no commits or evidence of shared/production deployment.

## Verification

- pytest: **19 passed** (one upstream TestClient deprecation warning).
- Ruff: **passed**.
- mypy: **Success: no issues found in 33 source files**.
- Migration: fresh SQLite upgrade, full downgrade to base, and re-upgrade succeeded.
- Schema drift: `python scripts/schema_drift.py` reports no drift between Alembic-created tables/columns and current ORM metadata.
- API: started against the newly migrated schema after a persisted development actor was provisioned; creation, legal transition, and audit retrieval returned `IMPORTED` and two audit events.
- Safe task semantics: heartbeat and both preview tasks executed directly and clearly reported safe/preview behavior.

## Docker and worker condition

`docker`/Docker Desktop is not installed, so Docker Compose parsing, image build, PostgreSQL health, Redis health, API container health, Celery worker connection, task dispatch through Redis, and clean container shutdown were not executable. Compose now waits for PostgreSQL and Redis where required, has safe restart settings, and `.dockerignore` excludes local secrets and generated data.

Run these commands in a Docker-enabled environment:

```powershell
Copy-Item .env.example .env
./scripts/dev.ps1 build
./scripts/dev.ps1 up
./scripts/dev.ps1 status
./scripts/dev.ps1 migrate
docker compose exec api python -m pytest
docker compose exec api python scripts/schema_drift.py /tmp/schema-drift.db
docker compose exec api python -m ruff check .
docker compose exec api python -m mypy app scripts
docker compose logs api worker
./scripts/dev.ps1 down
```

## Release and Milestone 2 readiness

The migration baseline is now deterministic and suitable for shared deployment. Milestone 1.1 remains **PASS WITH CONDITIONS** solely until Docker-backed PostgreSQL, Redis, API, and worker verification succeeds. Do not begin Milestone 2 until those commands have been run successfully.
