# Discord Client-Facing Visual QA Report

## Live-readiness conclusion

The redesigned Discord client experience is implemented locally and its code-level
checks have passed. It is **not yet verified as commercially complete in the live
guild**. The locally configured Discord bot credential was rejected with
`401 Unauthorized` before a safe `/setup` dry-run could connect to Discord, and
the live guild still showed visible legacy/demo clutter during inspection.

No live Discord resource was created, changed, or deleted by this QA pass.

## What is implemented locally

- Distinct, dark SaaS-style assets and concise panels for access, overview,
  workflow, plans, workspace, review, analytics, support, and Operations.
- A rules-first access path, public support entry point, member-only workspace
  area, staff-only Operations area, and private ticket flow.
- Human-readable workflow labels: **Source Added**, **Preparing Video**,
  **Ready for Review**, **Clips Ready**, **Content Package Ready**, **Ready for
  Decision**, **Published**, and **Needs Attention**.
- The supplied final Discord-native hero banner set is mapped to welcome,
  access, announcements, overview/how-it-works, plans, workspace, review,
  analytics, support, Operations, ready-for-decision, and private-ticket
  panels. Panel copy was shortened to let the banners carry the visual identity.
- Normal workflow cards no longer need to expose raw lifecycle values; technical
  diagnostics belong in staff-only Advanced Details.
- A non-destructive `/setup` cleanup preview that identifies legacy/demo
  candidates and points the owner to `/setup-reset` preview. It does not delete
  resources without a separate owner-approved action.

## What is blocked in live Discord

- The configured local Discord bot token returns `401 Unauthorized`, so the bot
  cannot connect to the real guild for final setup, panel publication, or visual
  verification.
- The observed guild has legacy/demo clutter ahead of the intended client
  journey: `YOUTUBE STUFF`, `#test`, `#review`, and an older start-area layout.
- The final live client journey, permission boundaries, ticket flow, workflow
  cards, and mobile layout remain unverified until the current revision is
  deployed and `/setup` succeeds against the real guild.

## Required next steps for a client-ready live Discord

1. Rotate or replace the Discord bot token in the Discord Developer Portal.
2. Put the new value in the protected deployed secret only; do not expose it in
   Git, Discord, logs, or screenshots.
3. Restart the deployed bot runtime and confirm it connects without `401 Unauthorized`.
4. As guild owner, run `/setup apply_changes: False` and review its plan and
   cleanup preview.
5. As guild owner, run `/setup apply_changes: True` to apply/repair managed
   resources.
6. Run `/setup-reset apply_changes: False` if the cleanup preview requires a
   complete legacy/demo candidate list.
7. Remove old demo/test clutter only after explicit owner approval for every
   candidate.
8. Complete the screenshot-based visual QA checklist on desktop and mobile, then
   update the verification table below with real evidence.

## Final hero banners installed

- `viralforge-welcome-hero.png`
- `viralforge-access-hero.png`
- `viralforge-announcements-hero.png`
- `viralforge-workflow-hero.png`
- `viralforge-plans-hero.png`
- `viralforge-workspace-hero.png`
- `viralforge-review-hero.png`
- `viralforge-analytics-hero.png`
- `viralforge-support-hero.png`
- `viralforge-ops-center-hero.png`
- `viralforge-ready-to-post-hero.png`
- `viralforge-ticket-hero.png`

These are registered in `ASSET_MANIFEST.md` and attached by the official panel
publisher only to the pages they support. The final supplied banners supersede
the earlier generated/temporary panel images for managed embeds.

## Local verification completed

- Discord page configuration resolves every managed channel and asset.
- The client journey defines public START/PLATFORM/CUSTOMERS/COMMUNITY pages,
  member-only WORKSPACES, staff-only OPERATIONS, and private ticket channels.
- Setup resource keys are unique; no managed test/demo channel is declared.
- Cleanup preview copy is regression-tested to list candidates without deletion.
- Normal lifecycle status labels are regression-tested for the required stages.
- Focused Discord tests, Ruff, Python compilation, and diff validation passed.

## Final live verification table

| Surface | Required live proof | Current state | Notes |
| --- | --- | --- | --- |
| Welcome | `#welcome` asset, concise CTA, rules path, and no legacy items above START | Pending | Blocked until credential is fixed and `/setup` runs. |
| Access | `#access` wizard, rules acceptance, and safe role explanation | Pending | Requires live panel publication and screenshot. |
| Overview | Distinct overview panel and readable CTA | Pending | Requires real-guild visual check. |
| Plans | Distinct plans panel and client-friendly access copy | Pending | Requires real-guild visual check. |
| Workspace guide | Visible only after acceptance; clear brand workflow | Pending | Requires permission and member-flow check. |
| Support | Public entry point creates a private ticket | Pending | Requires a safe end-to-end ticket test. |
| Ops center | Staff-only dashboard points to useful next actions | Pending | Requires staff-role visual check. |
| Workflow cards | Human-readable stages with no raw provider/state internals | Pending | Requires an active project flow check. |
| Advanced details | Technical diagnostics restricted to staff | Pending | Requires staff/non-staff boundary check. |
| Mobile sidebar | Clear, non-confusing category and channel names on mobile | Pending | Requires narrow/mobile screenshots. |
| Permissions | Public, member, staff, and ticket boundaries match policy | Pending | Requires test accounts or role-based inspection. |
| Ticket flow | Public support entry creates a private requester/staff ticket | Pending | Requires safe ticket creation and closure check. |
| Legacy clutter | Old demo/test channels are hidden or removed after owner approval | Failed — action required | `YOUTUBE STUFF`, `#test`, `#review`, and older layout were observed. |

## Commercial-completeness gate

The live Discord must not be called commercially complete until `/setup` has run
successfully against the real guild, all applicable table rows have live evidence,
and legacy/demo clutter is removed or hidden after owner approval.
