# Schema Hardening Report

## Scope and data safety

Released migrations `0001` through `0018` were not edited. Forward-only revisions `0019_schema_hardening` and `0020_checksum_index` reconcile the schema.

Before migration, local PostgreSQL contained one legacy workspace and one legacy brand; 11 production projects; zero unbranded projects, clips, content packages, publish requests, or analytics snapshots; zero invalid non-null `feed_subscriptions.active_job_id` values; zero duplicate non-null media checksums; and zero audit rows with a missing brand foreign-key target. No records were deleted or truncated.

## Exact inspected drift

| Area | Live schema | ORM metadata | Resolution |
|---|---|---|---|
| `audit_events.brand_id` | `uuid NOT NULL` | nullable UUID | ORM made non-null with legacy-brand default, matching the established backfill invariant. |
| Multi-brand indexes | Twelve `index=True` ORM indexes absent from `0016` | expected named indexes | `0019` creates the explicit named indexes. |
| `feed_subscriptions.active_job_id` | `varchar(36)` | UUID | `0019` validates all populated values then converts PostgreSQL to UUID; SQLite uses portable table recreation. |
| `ingestion_jobs.result_metadata` | PostgreSQL `json` | PostgreSQL JSONB variant | `0019` converts with explicit `result_metadata::jsonb`; SQLite remains JSON. |
| `media_assets.checksum` | unique `uq_media_assets_checksum` plus redundant normal index | normal ORM index | ORM now declares the named unique index; `0020` removes only the redundant normal index. |
| SQLite selected-source FK | omitted by historical cyclic-FK workaround | ORM expects FK | `0019` deterministically recreates the SQLite table to add `fk_projects_selected_source`. |

No additional column, foreign-key, nullability, enum, server-default, or index differences remained after reconciliation. PostgreSQL-specific UUID/JSONB behavior is explicit; SQLite uses portable UUID/JSON semantics and a batch table recreation for the cyclic foreign key.

## Migration and verification

- One head: `0020_checksum_index`.
- Local PostgreSQL upgraded from `0018_analytics_feedback` through `0019_schema_hardening` and `0020_checksum_index`.
- `alembic current`, `alembic heads`, and `alembic check` pass at the final head.
- `python scripts/schema_drift.py` passes on a clean SQLite base-to-head schema, including columns, nullability, expected indexes, and foreign keys.
- A disposable PostgreSQL database migrated cleanly from base to `0020_checksum_index`, passed `alembic check`, and was removed afterward.
- SQLite migration tests cover clean base-to-head, one head, named indexes, audit nullability, source-selection foreign key, downgrade to `0018`, re-upgrade, downgrade to base, and re-upgrade.
- The redundant `ix_media_assets_checksum` no longer exists; `uq_media_assets_checksum` remains.

## Files changed

- `alembic/versions/0019_schema_hardening.py`
- `alembic/versions/0020_remove_redundant_checksum_index.py`
- `app/audit/models.py`
- `app/content/models.py`
- `app/analysis/service.py`
- `scripts/schema_drift.py`
- `tests/test_migrations.py`
- `pyproject.toml`

## Runtime verification

Docker API and worker images were rebuilt. API `/health` and `/ready`, PostgreSQL, Redis, Celery ping/task registration, and Discord reconnect were verified after the final migration.
