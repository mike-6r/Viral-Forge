# Discord Visual QA Checklist

## Read this before live QA

The redesigned client journey is implemented locally. Its live verification is
blocked until the invalid Discord bot credential is rotated/replaced and the
deployed bot is restarted. The real guild was observed to contain legacy/demo
clutter, so this checklist must be completed after the successful real-guild
`/setup` run and owner-approved cleanup.

Do not mark the server commercially complete until every applicable live item
below is checked and the final result is recorded in
`DISCORD_VISUAL_QA_REPORT.md`.

## Required deployment and action checklist

- [ ] Rotate or replace the Discord bot token.
- [ ] Restart the deployed bot runtime and confirm a successful Discord login.
- [ ] Run `/setup apply_changes: False` as guild owner.
- [ ] Run `/setup apply_changes: True` as guild owner.
- [ ] Review the `/setup` cleanup preview.
- [ ] Run `/setup-reset apply_changes: False` if a deeper cleanup preview is needed.
- [ ] Remove old demo/test clutter only after owner approval.
- [ ] Complete this screenshot-based desktop and mobile QA pass.

## Client journey

- [ ] No legacy/demo channel appears above `01 — START`.
- [ ] `#welcome` reads as a concise product landing page with a single clear start path.
- [ ] `#access` looks like a setup wizard and explains that role choices do not grant staff authority.
- [ ] `#announcements` is read-only and uncluttered.
- [ ] `#overview`, `#how-it-works`, and `#plans` have distinct artwork and concise CTAs.
- [ ] `#workspace-guide`, `#review-and-publish`, and `#analytics` are visible only after rule acceptance.
- [ ] `#support` is publicly discoverable and opens a private ticket flow.
- [ ] Community channels are conversational rather than filled with repeated panels.

## Staff journey

- [ ] `#ops-center`, `#review-queue`, `#ready-to-post`, `#operator-alerts`, and `#ticket-logs` are not visible to non-staff.
- [ ] `#ops-center` points staff to Review Queue, Ready to Post, Add Video, Tickets, and Refresh.
- [ ] `/viralforge home`, `/viralforge review`, `/viralforge project`, and `/viralforge ready-to-post` show a clear next action.
- [ ] Normal cards show human-readable lifecycle labels only.
- [ ] Technical diagnostics and provider details are only shown through staff Advanced Details.
- [ ] Ready-to-post copy confirms an explicit human publishing decision is still required.

## Mobile check

- [ ] Category and channel names do not truncate confusingly.
- [ ] Each public panel has no more than three primary actions where possible.
- [ ] Workflow cards have no more than five standard actions.
- [ ] The key call to action appears before long explanatory text.

## Cleanup check

- [ ] Review every `/setup` legacy/demo candidate with the guild owner.
- [ ] Do not delete any candidate until ownership and ongoing use are confirmed.
- [ ] Use `/setup-reset apply_changes: False` before owner-confirmed cleanup.
- [ ] Capture before-and-after screenshots showing the legacy/demo clutter removed or hidden.
