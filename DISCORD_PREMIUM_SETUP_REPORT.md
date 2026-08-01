# ViralForge Premium Discord Setup Report

## Delivered

- Replaced the former broad community/control-plane layout with eight concise categories: Start Here, Platform, Workspaces, Content Ops, Customers, Community, Team, and Private Requests.
- Added 24 intentional channels, including forum channels for discoveries, case studies, feature requests, and staff customer review.
- Added professional staff, customer, workspace, and notification roles with restrained forge-themed colors.
- Added 15 managed product panels with an eyebrow, concise description, up to four fields, a VF thumbnail, a flat branded wide image, and contextual actions.
- Added persistent Rules, onboarding, support-ticket, and feature-request interactions. Ticket channels remain private to the requester and configured staff roles.
- Added a rules-first welcome gate: new members see only Start Here, accept the standards, receive the Member role, and then gain Platform, Workspaces, Content Ops, Customers, and Community. Team remains staff-only.
- Added the owner-only `/setup` command alongside `/admin setup-server`. Both create or refresh only ViralForge-managed resources, update managed panels, preserve unrelated channels and messages, and do not duplicate known resources.
- Added `/setup-reset` and `/admin setup-reset`: an owner-confirmed, dry-run-first cleanup for legacy ViralForge-managed resources. It includes old text channels that conflict with replacement forums, but excludes private ticket channels and current setup resources.
- Added configuration-backed rotating bot presence. Discord bot presences do not support application rich-presence images or buttons; the requested asset keys are retained in `config/discord/branding.yml` for Developer Portal configuration.

## Brand assets

Generated and attached original flat SaaS assets:

- Command Center / Welcome
- How It Works
- Multi-Brand Workspaces
- Human-Approved Review Pipeline
- Short-Form Distribution
- Performance Feedback Loop
- Support Request Routing
- Platform Standards
- Operating Plan
- Operations Control Center
- Square VF mark

Assets are local attachments under `assets/discord/viralforge`, so no customer or operational data leaves Discord through an image host.

## Verification

- `python -m pytest -q` — 118 passed.
- `python -m ruff check app/discord_business app/discord_bot.py tests/test_discord_business.py` — passed.
- `python -m mypy app/discord_business app/discord_bot.py` — passed.
- Configuration tests verify all 15 panels reference an existing managed channel and local asset, forum tags are present, and no panel exceeds Discord-friendly field/action counts.

## Live visual walkthrough

After deployment, the server owner should run `/setup apply_changes:true`, then inspect Start Here, Product Overview, How It Works, Pricing, Workspace Guide, Review Queue, Publishing Flow, Analytics, Support, Team Dashboard, and the three forums. This repository session cannot open the user’s authenticated Discord server, so that final visual inspection must occur after the bot reconnects.

## Remaining manual choices

- Verify the welcome gate with a non-staff account: before accepting rules it should see only `01 • START HERE`; after accepting, it should receive the `Member` role and see the member-facing categories. The next `/setup apply_changes:true` refreshes overwrites on existing managed resources.
- Upload `vf-icon.png` as the Discord server and bot icon if desired.
- The `viralforge_icon` and `status_online` rich-presence asset keys are documented in configuration for Developer Portal setup. They are not attachable by a Discord bot presence API.
- Review `/setup-reset` before confirming cleanup. It leaves unrelated server resources and all private ticket channels intact.
