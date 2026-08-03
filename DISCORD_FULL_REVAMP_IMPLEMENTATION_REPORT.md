# ViralForge Discord Full Revamp

## Result

The Discord setup layer has been rebuilt as a configuration-driven, review-first
business experience. The operational production pipeline, API, workers, data
model, and publishing safeguards were not redesigned or bypassed.

## Managed server layout

`/setup` now manages the following seven-category layout:

1. `01 — START` — `#welcome`, `#access`, `#announcements`
2. `02 — PLATFORM` — `#overview`, `#how-it-works`, `#plans`
3. `03 — WORKSPACES` — `#workspace-guide`, `#review-and-publish`, `#analytics`
4. `04 — CUSTOMERS` — `#onboarding`, `#support`, `#feature-requests`
5. `05 — COMMUNITY` — `#general`, `#creator-talk`, `#wins`
6. `06 — OPERATIONS` — `#ops-center`, `#review-queue`, `#ready-to-post`,
   `#operator-alerts`, `#ticket-logs`
7. `07 — PRIVATE REQUESTS` — private support tickets only

The public, workspace/member, staff-only, and private-ticket permission boundaries
are set from the centralized `config/discord` configuration. Existing unmanaged
server resources are preserved. Old ViralForge-managed/demo artifacts are only
removed after the owner explicitly confirms `/setup-reset`.

## Experience changes

- Reworked all public panels into short, product-oriented pages with one clear
  next action.
- Added clean access, onboarding, notification, support, feature request, and
  operations-center flows.
- Replaced legacy role terminology with the business role set: Member, Creator,
  Brand, Agency, Customer; Owner, Administrator, Operations Lead, Content
  Operator, Customer Success, Support Team, Developer; and five notification
  roles.
- Added a premium, minimal welcome banner at
  `assets/discord/viralforge/viralforge-welcome-v2.png`, registered in the asset
  manifest.
- Updated setup reporting to show managed resource totals, panel refresh totals,
  detected legacy/demo items, and a concise next step.
- Kept normal workflow cards human-readable. Raw project states remain behind
  staff-only advanced details rather than appearing in standard operator/customer
  screens.

## Safety and workflow behavior

- Public/community access does not grant workspace, operational, or publishing
  authority.
- Workspaces require the Member role after rule acceptance.
- Operations channels require a configured staff role.
- Ticket visibility remains restricted to the requester and configured support
  staff.
- `READY_TO_POST` remains a human publishing decision; no automatic publishing
  behavior was added.

## Verification

Completed locally:

- `python -m pytest -q` — passed, with two intentional skips.
- `python -m ruff check .` — passed.
- `python -m compileall -q app` — passed.
- Discord configuration validation tests now check the seven categories, twenty
  managed channels, unique resource keys, no managed test/demo channel names,
  public/member/staff boundaries, role selections, and premium panel assets.

The test environment emitted pre-existing dependency/schema teardown warnings;
they did not fail the suite. A live Discord-guild `/setup` run still requires the
deployed bot token and a guild-owner interaction, and was not simulated against a
real customer server during this local implementation pass.

## Files changed

- `app/discord_business/discord.py`
- `app/discord_business/service.py`
- `config/discord/*.yml` for branding, roles, permissions, categories, channels,
  panels, tickets, onboarding, announcements, and role synchronization
- `assets/discord/viralforge/viralforge-welcome-v2.png`
- `assets/discord/viralforge/ASSET_MANIFEST.md`
- `tests/test_discord_business.py`
