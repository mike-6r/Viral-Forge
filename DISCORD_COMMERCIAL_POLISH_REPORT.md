# Discord Commercial Polish Report

## Scope

This pass improves the client-facing Discord setup, onboarding panels, support entry point, operator presentation, and customer-facing review language. It does not change the production pipeline, publishing safeguards, database logic, or staff-permission model.

## Delivered

- Reframed the public structure as `01 - START` with `#welcome`, `#access`, and `#announcements`.
- Renamed the staff command-center channel to `#ops-center`.
- Kept the existing member gate: public visitors see the start area; member workspace areas require rules acceptance; team and private-request areas remain staff-only.
- Redesigned the public panels with shorter product copy, one clear next action, and a maximum of five buttons.
- Limited the logo, footer, and banner attachment to the welcome panel. Other product panels are intentionally flat, compact, and attachment-free.
- Made account-type selection clearer: Creator, Brand, and Agency are self-service profile choices. Operator and Support roles are explicitly staff-assigned and cannot be self-provisioned.
- Added the requested notification choices, including Workflow Alerts, Creator Tips, Case Studies, and Community Events.
- Added a support-topic selector before a private ticket is created. It offers Account / Access, Workspace Setup, Source or Video Issue, Publishing Issue, Billing / Plan, Bug Report, and Custom Workflow.
- Updated source opportunity review wording to be human-readable and action-oriented.
- Fixed `/account get-started` so it renders the configured welcome panel rather than a non-existent embed key.
- Added `youtube-stuff`, `test`, and `review` to the owner-confirmed legacy-cleanup list. The bot will never remove them during a normal setup refresh.

## Safe live reapply

After deploying this commit and restarting the Discord service, run these as the Discord server owner:

1. `/admin setup-server apply_changes: true` — applies the managed names, permissions, and official panels.
2. `/admin setup-reset apply_changes: false` — previews only the known legacy/demo channels that would be removed.
3. Review the preview carefully.
4. `/admin setup-reset apply_changes: true` — removes only the previewed legacy ViralForge-managed/demo resources. It preserves current managed resources, private tickets, and unrelated channels.

No channel or role was changed in the live Discord server by this repository pass.

## Verification

- `python -m pytest -q` — 163 passed, 2 skipped.
- `ruff check .` — passed.
- `mypy app` — passed.
- Discord business configuration tests — 8 passed.

The test suite emits existing dependency deprecation warnings and SQLite foreign-key teardown warnings; no new failure was introduced.
