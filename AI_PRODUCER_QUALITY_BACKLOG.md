# AI Producer quality backlog

This is based on the current evidence boundaries, not an unrun production acceptance test.

| Priority | Item | Evidence / disposition |
| --- | --- | --- |
| P1 | Complete real BodycamsDailyHQ acceptance and capture operator ratings. | External VPS/operator input is required; do not invent the result. |
| P2 | Improve rendered-frame subtitle styling/synchronization inspection. | Rendered Media Quality now adds bounded authoritative-media inspection, but OCR is disabled by default and exact burn-in typography/alignment still requires operator preview review. |
| P2 | Add a review-friendly source-candidate comparison summary. | The Producer can identify a higher persisted quality score, but comparative provenance presentation can be richer. |
| P2 | Support report-version comparison after an operator requests regeneration. | Current reports are versioned and retry-idempotent; a dedicated regeneration control is not yet exposed in Discord. |
| P3 | Add a compact quality-report detail screen in Discord. | The current report is concise and its reasoning states the evidence boundary. |
| P4 | Evaluate calibration across sufficient official analytics history. | Store comparisons first; do not implement autonomous learning or production-setting changes. |

No P0 defect is known from local verification. A P1 result remains possible until the real VPS/container and BodycamsDailyHQ acceptance run is completed.
