# ViralForge quality-of-life report

Date: 2026-08-02

## Fixed: recovery guidance on project dashboards

- Before: a pre-restart ephemeral project card could show Discord's "didn't respond in time" message, leaving an operator unsure how to continue.
- Improvement: every newly issued guided project card now states the recovery action: use `/viralforge home` to reopen active work after a bot restart.
- Technical boundary: Discord cannot make an already-expired or pre-restart ephemeral component interactive again. The recovery command creates a fresh, authorized view from persisted project state.
- Regression: Discord embed test asserts the recovery footer.

## Fixed: media-inspection card freshness

- Before: an already-issued media-quality card retained its queued snapshot after the worker finished inspection.
- Improvement: the card now includes **Refresh Status**, which reloads the persisted inspection and replaces the snapshot without re-running analysis.
- Safety: refresh is read-only; it does not generate a correction, change a clip decision, queue media, or publish anything.
- Regression: repository and view coverage verify persisted inspection retrieval and the refresh control.

## Current operator workflow

Guided cards retain one stage-specific primary action and consistent **Refresh Status** and **Back to Workspace** navigation. Slow background work is presented as a refreshable state rather than a duplicate action.
