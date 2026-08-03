# Unattended VPS operations guide

ViralForge runs from its Docker services: API, worker, one scheduler, Discord, PostgreSQL, and Redis. The scheduler performs bounded ticks and all durable state is stored in PostgreSQL, so the operator PC can remain off.

Verify restart policies, health checks, one scheduler, intended worker count, migration startup, registered task list, and Discord persistent-view registration after a deployment or reboot. Do not expose the database, Redis, credentials, or provider tokens. Keep MxF Labs and ports 80/443 unchanged.

Autopilot work is safe to restart because decisions, queue reservations, run-stage records, and provider requests are persisted. Ambiguous external outcomes are reconciled rather than retried blindly.
