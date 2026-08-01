# Discord command reference

Public commands: `/company about`, `/company features`, `/company plans`, `/company roadmap`, `/company docs`, `/company status`.

Community and customer commands: `/support open`, `/support feature`, `/support bug`, `/support history`, `/account get-started`, `/account subscription`, `/account billing-status`, `/account upgrade`, `/account usage`, `/account workspace`, `/account connect-account`.

Owner-only server configuration: `/setup`, `/setup-reset`, `/admin config-check`, `/admin setup-server`, `/admin setup-reset`, `/admin setup-status`, `/admin setup-repair`, `/admin refresh-embeds`, `/admin setup-export`, `/admin setup-reset-preview`.

Use `/setup-reset` first with the default `apply_changes: false` to see exactly which legacy ViralForge-managed channels, categories, and roles would be removed. Re-run it with `apply_changes: true` only after reviewing the private preview. It preserves current setup resources, tickets, and unrelated user-created channels.

Existing private operation commands remain under `/viralforge` and `/discovery`. They still require `VIRALFORGE_DISCORD_ALLOWED_ROLE_IDS`; public/community roles do not grant that capability.
