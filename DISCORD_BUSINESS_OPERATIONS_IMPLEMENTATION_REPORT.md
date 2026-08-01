# ViralForge Discord Business Operations Suite

## Delivered

- Forward-only migration `0023_discord_business_operations` extends the existing Discord business records.
- Private `/admin dashboard` uses section selection for support, customer/server, moderation, incidents, platform status, analytics, staff, and refresh views.
- Existing tickets now support state transitions, departments, tags, SLA timestamps, assignee records, private/customer-visible notes, escalation reasons, resolution timestamps, and satisfaction storage.
- Deterministic automod detects high-signal secret patterns, invite links, excessive mentions, and repeated messages. It stores only a pattern category and redacted metadata, opens a private case, and never persists a message body or secret.
- Added moderation cases, appeals, controlled role grants/expiry records, availability, announcement drafts, incidents, aggregate snapshots, and staff notes.
- `/roles` and `/account roles` offer only configured notification and interest roles. Customer, staff, operator, and subscription access remain provisioned, not self-assigned.
- Announcement sending requires the creator to explicitly run `/admin announcement-publish` with `confirm: True`.

## Boundaries

- Discord roles are not billing truth and do not authorize existing production controls by themselves.
- Staff dashboards are private and do not expose customer data, media, credentials, or raw moderation evidence.
- Automated moderation does not automatically kick or ban members.
- Scheduled announcements and temporary-grant cleanup are persisted and ready for the existing scheduler; only explicitly confirmed announcement publishing is enabled in this bot milestone.

## Verification

- Focused Discord tests, Ruff, and mypy pass locally.
