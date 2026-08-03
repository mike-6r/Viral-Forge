# Autopilot operator guide

Start from `/viralforge operations` after selecting the correct brand. The Automation field shows the active level, whether the brand is paused, reserved schedule slots, and outstanding exceptions.

Use the brand-scoped API or approved Operations controls to configure a policy. Every change needs an owner/admin, explicit confirmation in the client, and the policy version returned by the last read. A stale version is rejected rather than overwriting a newer safety setting.

- `MANUAL`: no creative, rendering, metadata, schedule, transfer, or post decision is automatic.
- `ASSISTED`: use scheduled discovery and advisory work only; source, clip, metadata, transfer, and post decisions remain human decisions.
- `SUPERVISED_AUTOPILOT`: policy may prepare sources, clips, inspection, packages, and schedules; a human confirms the final transfer or post.
- `AUTOPILOT`: disabled by default. It must have explicit policy values, destination ownership, rights/moderation evidence, limits, and provider authorization. Direct Post remains separately blocked until its official-provider validator is installed.

Resolve exceptions rather than deleting work. Hold or pause a brand during uncertainty. A scheduled reservation is not a publish request and has `confirmation_required=true` by default.
