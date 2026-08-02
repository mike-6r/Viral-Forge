# ViralForge production hardening report

Date: 2026-08-02

## Scope

This pass used the prior production acceptance reports as the source of truth. It improved confirmed safety and operator-experience gaps without redesigning the pipeline or enabling publishing.

## Issues fixed

1. **P1 worker render model registration** was already fixed and deployed in `4f2b19c`. A clean worker now registers correction-plan tables before `ProductionClip` mapper configuration.
2. **P2 missing sensitive-content warning** is fixed. The local deterministic package provider now adds a conservative warning only when a persisted source title contains `shooting`, `stabbing`, `homicide`, or `gunfire`. The warning cites source-title evidence and requires full-context review.
3. **P3 stale media-quality card** is fixed with a read-only **Refresh Status** action that reloads the persisted inspection snapshot.
4. **P3 restart recovery ambiguity** is improved with an explicit `/viralforge home` recovery footer on newly issued project dashboards.

## UX and safety improvements

- Content-package review now displays a dedicated **Sensitive-content review** field when warnings exist.
- Warning generation is intentionally narrow and evidence-bound; it does not infer a specific event from a transcript.
- Inspection refresh cannot re-run work or alter approvals, corrections, queueing, scheduling, or publishing.
- Existing publishing safeguards remain unchanged: no external upload, post, schedule, or destination action is created by these changes.

## Performance

No speculative performance tuning was applied. The prior VPS measurements showed a successful but CPU-intensive portrait render; no hang or duplicate work was observed. Refreshing an inspection card reads persisted state instead of starting another inspection.

## Validation

- Focused content-package and Discord tests passed.
- Full test suite, Ruff, and mypy passed locally.
- Disposable SQLite migration/schema comparison passed. Alembic autogeneration must be checked on PostgreSQL because SQLite reports expected UUID-type differences for the PostgreSQL schema.

## Remaining known limitations

Discord cannot revive an expired or pre-restart ephemeral component. Operators recover with `/viralforge home`; project state is persisted. The warning detector remains deliberately narrow to avoid inventing content classifications and is not a substitute for the existing human review gates.

## Deployment notes

The change has no migration. It was deployed to the active IP-bootstrap profile in commit `ecbf221` after a PostgreSQL backup. The VPS Alembic upgrade and PostgreSQL `alembic check` completed with no new upgrade operations. API health and readiness returned OK; PostgreSQL and Redis were healthy; Celery ping and task registration succeeded; and Discord reconnected to the gateway. The deployed container also passed the title-evidence warning probe.

Only the API, worker, scheduler, Discord, and migration services were rebuilt or recreated. The protected `.env.ip-bootstrap` file, database and Redis volumes, Caddy, and ports 80/443 were preserved.
