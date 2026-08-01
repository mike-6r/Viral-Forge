# ViralForge Discord server setup

1. Invite the configured ViralForge bot with `Manage Roles`, `Manage Channels`, `Send Messages`, and `Use Application Commands`. Place its highest role above the ViralForge roles it creates.
2. In the target Discord server, the actual server owner runs `/admin config-check`, then `/admin setup-server` with `apply_changes` left false. This is a non-mutating dry run.
3. Review the plan. The server owner alone may rerun `/admin setup-server apply_changes:true` to create only missing named resources. Existing resources are retained and persisted IDs prevent duplicates.
4. Run `/admin refresh-embeds` after setup. It posts only official information messages and does not delete community discussion history.
5. Run `/admin setup-status` and `/admin setup-export` to verify the configuration. `/admin setup-repair` uses the same idempotent plan.

Never give public, customer, or community roles the existing operator role IDs. The pre-existing `/viralforge` and `/discovery` controls retain their separate configured authorization gate.
