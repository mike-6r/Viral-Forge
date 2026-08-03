# Discord Visual QA Checklist

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

- [ ] Run `/setup` preview and review every reported legacy/demo candidate.
- [ ] Do not delete any candidate until ownership and ongoing use are confirmed.
- [ ] Use `/setup-reset` preview before owner-confirmed cleanup.
