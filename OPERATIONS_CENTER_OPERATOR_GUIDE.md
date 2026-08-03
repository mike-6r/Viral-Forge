# Operations Center Operator Guide

The Operations Center remains the primary place to decide what to do next. Select a brand first, then use `/viralforge operations` for its compact health, queue, tasks, and alerts summary.

- **Healthy**: no immediate operational action is required.
- **Attention Needed**: review content or a normal-priority task.
- **Degraded / Critical**: inspect the corresponding operator task and the existing service health checks before processing new work.

The button and command do not publish, retry uploads, or dismiss source/clip approvals. Continue to make those creative and publishing decisions through their existing explicit review controls.

## Autopilot controls

Operations now shows a brand's automation level, pause state, reserved schedule count, and exception count. Policy changes, emergency pauses, queue ranking, and schedule reservations are brand-scoped and versioned. A missing fact or unsafe policy routes work to the exception inbox; it never silently changes a clip or uploads content.
