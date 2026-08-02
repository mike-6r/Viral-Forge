# AI Producer operator guide

The Producer advises; it never starts a download, analysis, render, queue action, schedule, upload, or publishing request.

## Project advice

Open a project in Discord and choose **Producer Advice**. Review one concise recommendation at a time:

- confidence is shown as Low, Moderate, or High with the bounded numeric value;
- strongest stored evidence is summarized, while **More Details** keeps supporting evidence out of the default view;
- use **Add / Edit Note** to record your judgment before deciding;
- **Approve** and **Reject** record only the advice decision; they do not mutate the pipeline;
- use **Back** or **Home** to return safely.

Review source trust, whether an alternative source is better, process readiness, recommended clip count/boundary, metadata completeness, and publish readiness against the persisted source and transcript. Never treat the advice as rights approval or an instruction to publish.

## Finished-clip quality report

From a finished clip choose **Quality Report**. It summarizes hook, pacing, context, transcript-based subtitle coverage, title, caption, hashtags, overall readiness, and a retention prediction. Choose **Media Quality** for a separate inspection of the actual authoritative rendered asset.

Retention is a prediction, not an actual metric. Subtitle quality is a transcript-coverage proxy unless a completed Media Quality inspection is listed in the evidence. Use the report to guide the existing review controls, not to bypass them.

## Safe review expectations

- A stale review version is rejected rather than overwriting a newer decision.
- Repeated approve/reject decisions are idempotent.
- Advice and quality reports remain scoped to the project’s Brand.
- Later official analytics snapshots may be compared with stored predictions, but no automatic tuning occurs.
