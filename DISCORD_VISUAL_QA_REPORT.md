# Discord Client-Facing Visual QA Report

## Scope and live check result

The locally configured Discord client was inspected in the real `Viral Forge`
server. The current visible server is still an older deployment rather than the
new managed layout. A local bot start was attempted to run a safe `/setup`
equivalent dry-run, but Discord rejected the locally configured credential with
`401 Unauthorized` before a guild connection was made. No live server resource
was created, changed, or deleted by this QA pass.

## Issues found in the live client

- `YOUTUBE STUFF`, `#test`, and `#review` remain visible before the real client
  journey.
- An earlier `01 - START HERE` layout remains alongside the intended structure.
- The visible content-package card exposed the raw `PENDING` state.
- The current live setup therefore does not yet prove the premium first
  impression, role flow, or managed panel artwork delivered in this revision.

## Changes made locally

- Connected a distinct image banner to each major client-facing page: access,
  overview, workflow, plans, workspace, review, analytics, support, and the
  Operations Center.
- Reworked lifecycle presentation so normal cards show Source Added, Preparing
  Video, Ready for Review, Clips Ready, Content Package Ready, Ready for
  Publishing Decision, Published, or Needs Attention rather than raw database
  values.
- Replaced normal content-package and rendered-media status output with plain
  customer/operator wording. Technical status remains appropriate only inside
  staff Advanced Details.
- Extended `/setup` output with a non-destructive cleanup preview that lists up
  to eight detected legacy/demo resources, reports any additional candidates,
  and points the owner to `/setup-reset` preview. It cannot delete anything
  without a separate owner confirmation.

## Assets added

- `viralforge-access.png`
- `viralforge-overview.png`
- `viralforge-workflow.png`
- `viralforge-plans.png`
- `viralforge-workspace.png`
- `viralforge-review.png`
- `viralforge-analytics.png`
- `viralforge-support.png`
- `viralforge-ops-center.png`

These are registered in `ASSET_MANIFEST.md` and attached by the official panel
publisher only to the pages they are intended to support. The access and workspace
banners were generated for this pass; the remaining files are matched,
page-specific assets from the existing ViralForge visual system, published under
the final names.

## Verification completed

- Discord page configuration resolves every managed channel and asset.
- The client journey contains public START/PLATFORM/CUSTOMERS/COMMUNITY pages,
  Member-only WORKSPACES, staff-only OPERATIONS, and private ticket channels.
- Setup resource keys are unique; no managed test/demo channel is declared.
- Cleanup preview copy is regression-tested to list candidates without deletion.
- Normal lifecycle status labels are regression-tested for the required stages.
- Focused Discord tests, Ruff, Python compilation, and diff validation passed.

## Remaining live blocker

Rotate or replace the invalid local Discord bot credential, deploy this revision
to the bot runtime, then run `/setup` with `apply_changes: True` as the guild
owner. Review the cleanup preview first; use `/setup-reset` preview and only
then a separately confirmed cleanup if `YOUTUBE STUFF`, `#test`, `#review`, and
the old setup are no longer needed.

Until that deployment and owner-reviewed cleanup occur, it would be inaccurate to
confirm that no test/demo clutter is visible or that the current live server fully
feels like the finished professional platform.
