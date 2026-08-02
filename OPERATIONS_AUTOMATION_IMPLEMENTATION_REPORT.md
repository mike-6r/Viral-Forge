# ViralForge Production Intelligence & Operations Automation

## Scope delivered

- Brand-scoped daily schedule configuration on `ContentProfile`.
- Bounded Celery operations refresh every five minutes.
- Persisted, once-per-brand/local-day morning briefings and evening reports with explicit Discord delivery state.
- Persisted, duplicate-suppressed alerts and operator tasks.
- Queue metrics, brand health score, briefing, evening report, and audit timeline APIs.
- Discord `/viralforge operations` summary plus Operations Center access from advanced controls.

## Safety boundary

The operations task is read-mostly. It creates only `OperatorTask` and `OperationsAlert` records. It does not create a publish request, schedule an upload, or call any publishing provider.

## Migration

Revision `0030_operations_automation` adds the optional `operations_schedule_json` configuration and the `operations_alerts` / `operator_tasks` tables. Revision `0031_operations_daily_reports` adds deduplicated report delivery records. Both are forward-only and have deterministic downgrades.

## Verification

Run `pytest`, Ruff, mypy, `alembic check`, and `python scripts/schema_drift.py` before deployment. Docker/VPS verification requires the production environment and is documented in the deployment block supplied with the release.
