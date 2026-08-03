# ViralForge Discord Admin Guide

## Apply or repair the managed setup

Only the Discord server owner can apply the layout:

1. Run `/setup` with `apply_changes: False` to inspect the plan.
2. Run `/setup` with `apply_changes: True` to create or repair managed roles,
   categories, channels, permissions, panels, and views.
3. Use `/admin refresh-embeds` after a copy or asset change.

The setup is idempotent: managed resources are matched by stored Discord IDs before
names, so reruns repair existing resources rather than duplicating them.

## Clean up legacy/demo clutter safely

`/setup` now includes a private cleanup preview whenever it detects old
ViralForge-managed names such as `#test`, `#review`, or `YOUTUBE STUFF`-era
workflow channels. It lists candidates and recommends the next step; it does not
delete them.

To review the full candidate list, run `/setup-reset` with `apply_changes: False`.
Only after verifying the list, run it again with `apply_changes: True`.

This cleanup does not remove unmanaged channels, active private tickets, or
non-empty legacy categories automatically.

## Permission model

- **Public:** START, PLATFORM, CUSTOMERS, and COMMUNITY.
- **Member:** WORKSPACES after rule acceptance.
- **Staff:** OPERATIONS, including diagnostics and operator controls.
- **Private request:** ticket requester plus configured ticket staff only.

Staff roles are Owner, Administrator, Operations Lead, Content Operator, Customer
Success, Support Team, and Developer. Customer/community roles never grant
operator or publishing authority.

## Routine visual check

After a deployment, check `#welcome` and `#access` in desktop and narrow/mobile
layouts. Confirm the two screens have the correct asset, concise copy, and usable
buttons. Then check `#ops-center` as staff and `/viralforge home` for the active
brand's live workload.

