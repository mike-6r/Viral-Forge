# Discord Operations Commands

All `/admin` commands re-check staff authorization when executed. Server setup/reset remains owner-only.

- `/admin dashboard` — private section-based operations dashboard.
- `/admin ticket-list`, `/admin ticket-overdue`, `/admin ticket-assign`, `/admin ticket-note`, `/admin ticket-status` — durable support operations.
- `/admin member-note` — staff-only member note.
- `/admin moderation-cases` — redacted case queue.
- `/admin role-temporary` — requires `confirm: True`, honors member and bot role hierarchy.
- `/admin staff-availability` — `AVAILABLE`, `BUSY`, `AWAY`, or `OFF_DUTY`.
- `/admin incident-create` — internal incident record; no infrastructure information is public.
- `/admin announcement-create` — creates a draft only.
- `/admin announcement-publish` — requires `confirm: True` and publishes only its configured draft/channel.
- `/admin analytics-snapshot` — aggregate, privacy-safe daily snapshot.

Use `/account dashboard`, `/account roles`, `/account appeal`, or `/roles` for member-scoped controls.
