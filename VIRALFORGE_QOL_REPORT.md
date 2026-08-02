# ViralForge quality-of-life report

Date: 2026-08-02

## Fresh project dashboard after a bot restart

- Current behavior: a pre-restart ephemeral project card can show Discord's "didn't respond in time" message after the bot container is recreated.
- Why it is confusing: the backing project can be healthy while the old card is no longer actionable.
- Suggested improvement: explicitly state on ephemeral dashboard cards that a post-restart recovery command is `/viralforge project <project_id>`, or expose a non-ephemeral project link where appropriate.
- Implemented: no. This is a Discord ephemeral-message limitation; the existing fresh slash command successfully recovered the project dashboard.
- Deferred reason: preserving the current approval-first workflow and avoiding unrelated Discord UX redesign during acceptance testing.

## Media-inspection card freshness

- Current behavior: an already-issued ephemeral media-quality card retains its queued snapshot after inspection completes.
- Why it is confusing: the completed inspection exists, but the old card does not refresh itself.
- Suggested improvement: add a clearly labeled Refresh inspection action or state that the card is a snapshot.
- Implemented: no.
- Deferred reason: non-blocking; the persisted inspection completed and the workflow continued safely.
