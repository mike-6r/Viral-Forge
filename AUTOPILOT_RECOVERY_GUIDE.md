# Autopilot recovery guide

The watchdog classifies stale work as safe retry, reconcile first, operator required, or blocked. Only work with no possible external side effect may be retried automatically. Provider transfers with uncertain outcomes must be reconciled using the official provider status before any retry.

Use the global emergency control for an incident, or pause only the affected brand. These controls stop new work and retain queue, audit, provider, and schedule state. They do not cancel a potentially in-flight provider transfer when cancellation could make its outcome ambiguous.
