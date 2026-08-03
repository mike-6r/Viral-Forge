# Operator Briefings Guide

`GET /api/v1/operations/briefing` provides a concise previous-24-hour summary: videos found, clips rendered, approved content packages, health, and up to three priority actions. `GET /api/v1/operations/evening-report` adds queue health and a recommendation.

Both endpoints are brand-scoped and require an authenticated member of that brand. They report persisted facts only; unavailable social metrics are omitted rather than fabricated.

Discord operators can use `/viralforge operations` for the same compact overview. Notifications should be sent only to the configured operations/review channel by the existing Discord setup, never in a repeated loop.

When the scheduler reaches the configured local briefing/report time, it persists one report per brand and local date. The Discord bot polls pending reports every five minutes, posts each one in the configured review channel, and records the message ID only after delivery succeeds. Failed delivery remains pending for a later retry; duplicate summaries are not created.
# Autopilot briefing extension

Daily and evening reports remain persisted and deduplicated by brand/date. The Operations summary provides the current automation level, scheduled content count, and exception count. Treat exceptions as operator work: review rights, moderation, quality, destination, provider, or stale-job evidence before overriding a decision.
