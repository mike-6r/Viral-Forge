# Operator Briefings Guide

`GET /api/v1/operations/briefing` provides a concise previous-24-hour summary: videos found, clips rendered, approved content packages, health, and up to three priority actions. `GET /api/v1/operations/evening-report` adds queue health and a recommendation.

Both endpoints are brand-scoped and require an authenticated member of that brand. They report persisted facts only; unavailable social metrics are omitted rather than fabricated.

Discord operators can use `/viralforge operations` for the same compact overview. Notifications should be sent only to the configured operations/review channel by the existing Discord setup, never in a repeated loop.
