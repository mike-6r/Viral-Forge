# ViralForge release readiness

Date: 2026-08-02

## Ready paths

- Dockerized API, worker, scheduler, Discord bot, PostgreSQL, and Redis started on the VPS.
- Health, readiness, worker ping, task registration, Discord recovery, manual intake, source processing, analysis, rendering, private preview, inspection, content packaging, and content-ready queueing were verified.
- The single acceptance-blocking worker defect was fixed in commit `4f2b19c`, deployed, and verified by a successful retry.
- No public publishing action was performed.

## Release decision

The tested approval-first workflow is ready through **CONTENT_READY**. Public publishing remains intentionally gated by the existing explicit destination and human-decision workflow.

## Production hardening update

The local content-package provider now adds evidence-labeled sensitive-content review warnings for direct high-signal terms in persisted source titles. Discord media-quality cards can refresh their persisted state, and newly issued guided project cards explain the safe post-restart recovery route. These changes are backward-compatible and do not create an upload, schedule, or publish action.

The hardening commit `ecbf221` is deployed on the VPS. PostgreSQL migration and Alembic checks, API health/readiness, Redis, Celery ping/task registration, and Discord gateway reconnection succeeded.
