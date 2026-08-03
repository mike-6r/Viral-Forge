# ViralForge Discord Admin Guide

## Live-readiness status

The managed Discord configuration, panels, assets, workflow wording, cleanup
preview, and regression coverage are implemented locally. Final live deployment
is currently blocked because the locally configured Discord bot credential is
rejected by Discord with `401 Unauthorized` before it can connect to the guild.

The current real guild also has legacy/demo clutter that requires an owner-reviewed
cleanup. Do not claim commercial live readiness until `/setup` has completed
successfully against the real guild and the old visible clutter has been removed
or hidden with owner approval.

## Required deployment and action checklist

Complete these steps in order as the guild owner or a designated administrator:

- [ ] Rotate or replace the Discord bot token in the Discord Developer Portal.
- [ ] Store the replacement only in the protected deployment secret or environment.
- [ ] Restart the deployed bot runtime.
- [ ] Confirm the bot logs in and reconnects without a `401 Unauthorized` error.
- [ ] Run `/setup apply_changes: False` and retain the private plan/cleanup preview.
- [ ] Run `/setup apply_changes: True` as the guild owner to apply or repair the
  managed roles, categories, channels, permissions, panels, and views.
- [ ] Review the cleanup preview from `/setup`.
- [ ] Run `/setup-reset apply_changes: False` if a detailed legacy-cleanup preview
  is needed.
- [ ] Remove old demo/test clutter only after the owner approves each candidate.
- [ ] Complete the desktop and mobile screenshot-based visual QA checklist.

## Apply or repair the managed setup

Only the Discord server owner can apply the layout:

1. Run `/setup` with `apply_changes: False` to inspect the plan.
2. Run `/setup` with `apply_changes: True` to create or repair managed roles,
   categories, channels, permissions, panels, and views.
3. Use `/admin refresh-embeds` after a copy or asset change.

The setup is idempotent: managed resources are matched by stored Discord IDs
before names, so reruns repair existing resources rather than duplicating them.

## Discord bot token troubleshooting: 401 Unauthorized

A `401 Unauthorized` error during bot startup means Discord rejected the token
before the bot connected to any guild. Treat the token as compromised or invalid.

1. Open the application's **Bot** page in the Discord Developer Portal and
   reset or copy a newly generated token.
2. Replace the protected deployment value for the bot token (normally
   `VIRALFORGE_DISCORD_BOT_TOKEN`) on the VPS or secret manager. Do not add it to
   Git, ordinary database fields, public `.env` examples, Discord messages, or
   screenshots.
3. Verify the deployed value has no copied quotes, whitespace, newline, or old
   token value.
4. Restart only the deployed Discord bot runtime, then inspect its logs for a
   successful login/reconnect message. Never print the environment or token while
   diagnosing it.
5. If it still returns `401`, generate another token, update the protected secret,
   and restart again. Confirm the correct Discord application and bot were used.
6. If a token may have appeared in terminal output, an image, source control, or a
   chat, rotate it immediately and invalidate the exposed value.

Once the bot is connected, continue with the `/setup` preview before applying any
live change.

## Clean up legacy/demo clutter safely

`/setup` includes a private cleanup preview whenever it detects old
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

After the final deployment, check `#welcome` and `#access` in desktop and
narrow/mobile layouts. Confirm the correct asset, concise copy, and usable
buttons. Then check `#ops-center` as staff and `/viralforge home` for the active
brand's live workload. Record the result in `DISCORD_VISUAL_QA_REPORT.md`.
