# ViralForge Discord server setup

1. Invite the configured ViralForge bot with `Manage Roles`, `Manage Channels`, `Send Messages`, and `Use Application Commands`. Place its highest role above the ViralForge roles it creates.
2. In the target Discord server, the actual server owner runs `/admin config-check`, then `/admin setup-server` with `apply_changes` left false. This is a non-mutating dry run.
3. Review the plan, then run `/admin setup-server apply_changes:true`. It renames and moves current managed channels in place, creates only missing resources, and updates existing official embeds rather than duplicating them.
4. Run `/admin setup-reset` with `apply_changes` left false. Review the obsolete managed resources it identifies. Only then run `/admin setup-reset apply_changes:true` to remove those obsolete managed resources. It never targets unrelated channels or private ticket channels.
5. Run `/admin refresh-embeds`, then `/admin setup-status` and `/admin setup-export`. Refresh posts or updates only official information messages; it does not delete community discussion history.

Never give public, customer, or community roles the existing operator role IDs. The pre-existing `/viralforge` and `/discovery` controls retain their separate configured authorization gate.
