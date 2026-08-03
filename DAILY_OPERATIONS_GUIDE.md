# Daily Operations Guide

Each brand may set `operations_schedule_json` on its content profile. Supported keys are `timezone`, `quiet_hours` (`[{"start":"22:00","end":"06:00"}]`), `holidays` (ISO dates), `pause_windows` (ISO dates), discovery/processing/review intervals, briefing and evening-report hour, plus publishing and maintenance windows.

The scheduler checks brands every five minutes. Quiet, holiday, and pause periods suppress the operations refresh; they do not delete work or alter existing pipeline jobs. Discovery remains governed by the existing discovery scheduler and source polling intervals.

Use `/viralforge operations` to inspect the selected brand. Use `/viralforge review` for the next creative decision. No operation screen publishes content.

Autopilot briefings use persisted operations facts. They summarize automation state, queue/schedule condition, content requiring attention, and health; unavailable analytics remain unavailable rather than estimated.
