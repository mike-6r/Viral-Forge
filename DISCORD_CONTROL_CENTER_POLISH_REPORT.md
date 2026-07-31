# Discord Control Center Polish Report

## Scope and outcome

Completed the P0 Discord operator-control polish without changing the production
architecture, discovery behavior, rendering policy, or publishing behavior. The
existing audited domain services remain the only code path for source decisions,
downloads, analysis, opportunity decisions, clip decisions, and queue creation.

## Completed

### P0 control center and lifecycle

- Added `/viralforge home`, a shared persistent Discord control-center message.
  Discord reserves `/viralforge` as the existing command group, so the compatible
  command is `/viralforge home` rather than a conflicting root command.
- The home view reports live, read-only counts for enabled discovery sources,
  discovery review, projects, source review, downloaded/awaiting-analysis work,
  queued/running/completed/failed analysis, opportunity review, clip review,
  posting queue, and project failures.
- Added persistent buttons: Discovery, Projects, Source Review, Analysis,
  Opportunities, Clips, Queue, System Status, and Refresh.
- Added a project picker and project list, including a filtered source-review
  inbox. Project detail retains the existing complete lifecycle in one view.
- Project controls now render disabled when the current state makes the action
  invalid. Refresh re-evaluates that state. Stale persisted messages remain safe:
  the service layer rejects invalid actions and the bot explains how to recover.
- Added `Analyze Source` to the existing project dashboard. It uses the existing
  persisted analysis request and Celery task; it does not introduce a new worker
  workflow.
- Added Home navigation to project and discovery views, and Back to Project to
  opportunity review. Existing clip review already provides Back to Project and
  Previous/Next navigation.
- Replaced raw operator-facing domain error codes in the principal persistent
  workflows with concise safe messages containing what happened, recovery steps,
  and a reference code. Permission failures explain the configured-role remedy
  without exposing role IDs.
- Registered the control-center persistent view during bot startup alongside the
  existing project, clip, and opportunity persistent views.

### P1 inbox affordances completed safely

- Added `/viralforge review`, which opens the next pending opportunity, then
  pending rendered clip, then discovery item, or reports an empty inbox.
- Added `/viralforge projects` for the recent-project picker.

### Reliability and audit behavior

- Hardened repeated source accept/reject/selection, clip decisions, and
  opportunity decisions so same-outcome interaction retries are idempotent. They
  do not add duplicate queue records or duplicate audit events.
- Existing service-level audit events continue to record every state-changing
  decision. Dashboard navigation, status, and refresh remain read-only.
- Authorization remains centralized through the existing owner-or-configured-role
  policy and is applied to all new commands and controls.

## Commands and navigation

- `/viralforge home` — persistent shared control center.
- `/viralforge projects` — recent project picker.
- `/viralforge review` — unified pending-review entry point.
- Existing `/viralforge submit`, `/viralforge project`, `/viralforge queue`, and
  `/viralforge status` remain compatible.
- Existing `/discovery status`, `/discovery queue`, `/discovery approve`, and
  `/discovery reject` remain compatible.

## Persistence and notifications

All new component custom IDs are stable and the control-center view is registered
at startup. Existing project/clip/opportunity messages are re-registered using
their persisted records. No new notification loop, download, clip generation, or
posting action is started by the control center itself.

## Verification

- Ruff: passed.
- mypy: passed for all 59 application source files.
- pytest: passed, 84 tests.
- Schema drift: passed against a fresh Alembic-created SQLite database.
- Docker build: rebuilt API and worker images successfully.
- Runtime: API `/health` returned `{"status":"ok"}` and `/ready` returned
  `{"status":"ready"}`.
- Runtime: PostgreSQL and Redis were healthy; database migration revision was
  `0013_clip_opportunities`.
- Runtime: Celery worker answered `inspect ping` and registered heartbeat,
  discovery, analysis, and opportunity tasks.
- Runtime: Discord bot connected to the gateway after restart without an
  exception. Its setup hook registers persistent views and performs the existing
  guild command sync.

## Files changed

- `app/discord_bot.py`
- `app/production/service.py`
- `app/opportunities/service.py`
- `tests/test_discord_bot.py`
- `tests/test_production.py`
- `DISCORD_CONTROL_CENTER_POLISH_REPORT.md`

## Migrations

None. This is a UI/control-plane and idempotence polish that reuses the existing
persisted schema.

## Intentionally deferred

P2/P3 work was not started: no new notifications, external publishing controls,
advanced analytics, or architectural redesign was added. Confirmation modals for
additional bulk actions were also left unchanged to preserve the existing control
surface rather than introduce partially tested behavior.

## Limitations and next recommendation

Each authorized `/viralforge home` invocation creates a persistent shared
dashboard message in its current channel. A single canonical, database-tracked
home message would require a new persisted setting and is intentionally outside
this polish scope. The recommended next step, only if product scope permits, is
to decide whether that canonical-message configuration is desired before adding
it.
