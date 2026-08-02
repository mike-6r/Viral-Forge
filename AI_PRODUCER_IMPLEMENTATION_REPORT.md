# AI Producer implementation report

## Outcome

ViralForge now contains an approval-first AI Producer layer positioned between discovery/analysis and publishing. It adds recommendations and quality reports; it does not replace or auto-advance the existing pipeline.

## What it recommends

- Source trust and whether a source is suitable to review for downloading.
- Processing readiness, clip count, priority opportunity, and proposed clip boundaries.
- Metadata readiness through the existing evidence-bound content package.
- Finished-clip quality: hook, pacing, context, retention estimate, subtitles, title, caption, hashtags, and overall readiness.

Each recommendation stores a confidence score, reasoning, structured persisted evidence, editable operator notes, prediction fields, provider/model/version, an optimistic review version, and an audit event. Approving or rejecting one records the decision only; it never downloads, renders, queues, schedules, or publishes anything.

## Evidence and learning boundary

The default provider is deterministic and only reads persisted source-quality data, analysis, transcripts, analysis events, opportunity reasons, rendered timing, and content-package fields. It makes no factual claims outside that evidence. A scheduled task compares stored predictions with official analytics snapshots when they exist, and stores the comparison without tuning production settings.

## Integration points

- Completed analysis enqueues advisory project recommendations.
- A successfully rendered clip enqueues a quality report.
- API routes support generation, listing, review decisions with optimistic locking, and quality-report retrieval.
- Discord exposes `Producer Advice` on a project card and `Quality Report` on a completed clip card.

## Safety

- No project state changes occur on recommendation generation or approval.
- No publishing action is initiated by the Producer layer.
- All records are brand-scoped and audited.
- External credentials and raw secrets are not stored or read by the Producer layer.
