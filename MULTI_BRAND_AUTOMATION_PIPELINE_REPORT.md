# Multi-Brand Automation Pipeline

## Orchestration

ViralForge now keeps the operator at the creative decision points while the existing worker performs the mechanical handoffs:

1. Source acceptance queues the idempotent `process_accepted_source` task for download, inspection, transcription, analysis, and opportunity generation.
2. Opportunity approval queues `render_approved_opportunity`, which renders through the existing approved-opportunity service and prepares an optional private preview proxy.
3. Rendered-clip approval queues the existing content-package generator for evidence-bound titles, captions, descriptions, hashtags, and platform variants.
4. Content-package approval remains a human gate. Existing publishing safeguards still require an explicit destination account and an explicit publish or schedule confirmation; no new path publishes automatically.

## Brand ownership and configuration

No schema redesign was required. Existing `Brand`, `ContentProfile`, `BrandingProfile`, `ReviewPolicy`, `PostingPolicy`, `SourceAccount`, and `DestinationAccount` records remain the operating-profile boundary. Production, discovery, analysis, opportunities, clips, content packages, posting queue entries, publishing requests, analytics, and audit records continue to carry a brand directly or through their project.

Destination accounts are brand-owned and continue to store only credential reference IDs, never credentials. Existing publishing validation rejects cross-brand destinations.

## Automation levels

The current persisted brand profile/policy records support the existing operational modes:

- Manual: human approvals at each review stage.
- Assisted: background mechanical processing after the source/opportunity/clip decisions.
- Maximum automation: the same background processing plus automatic metadata generation and queue preparation, while publishing remains explicitly confirmed by a human.

## Operator experience

The guided Discord home remains limited to the production actions. Project cards now include a compact pipeline visualization for source, download, analysis, suggested clips, rendering, and content-ready status. Worker transitions do not create noisy Discord messages; humans are notified only when a review decision is available.

## Verification

Local verification on 2026-08-01:

```text
python -m pytest -q       PASS
python -m ruff check .    PASS
python -m mypy app        PASS
```

Coverage includes Celery registration for the new render task, existing source-to-analysis and content-package task registration, multi-brand API isolation, destination-account ownership, and publishing rejection for cross-brand destinations.

## Deployment

No migration is required. The update is ready for the existing VPS compose workflow. Live deployment and Discord verification remain blocked from this machine because SSH to `198.51.178.178:22` times out; the application code and Git remote are unaffected.

## Remaining limitations

- Private preview proxy generation remains configuration-controlled and intentionally does not alter the rendered source clip.
- Destination-account OAuth connection remains the existing secure external-credential workflow.
- Publishing stays disabled unless separately configured and explicitly confirmed.
