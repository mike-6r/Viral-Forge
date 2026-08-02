# Health Monitoring Guide

The operations layer evaluates persisted queue latency, failed projects, review backlog, and destination readiness into `Healthy`, `Attention Needed`, `Degraded`, or `Critical` brand states. It groups recurring symptoms into one open task or alert per brand and category.

Infrastructure checks remain exposed through the existing API readiness endpoint, Compose health checks, Celery heartbeat, scheduler heartbeat, and Discord persistent-view startup. Do not put credentials, provider tokens, raw environment values, or backup locations in alerts.

Escalation is operator-facing only: investigate the underlying service, then resolve the related task/alert through the normal operator workflow. It never retries or publishes silently.
