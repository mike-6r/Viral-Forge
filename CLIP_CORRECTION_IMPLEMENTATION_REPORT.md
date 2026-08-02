# Clip correction implementation report

Migration `0029_clip_correction_workflow` adds brand-scoped, optimistic-locking correction plans and immutable action rows. A confirmed plan creates a separate `ProductionClip` revision and authoritative `MediaAsset`; it never overwrites the original, changes approval, creates a queue item, or publishes.

Supported local controls are bounded opening/ending trims and presentation-audio normalization, peak limiting, or bounded gain. Unsupported subtitle, crop, overlay, watermark, and evidence-overlay changes are explicitly represented as manual-review advice rather than being guessed or simulated. The worker performs no action until the plan is submitted and then separately confirmed.

The worker queues the existing rendered-media inspection for a revised clip. Before/after comparison uses completed inspections only and reports `Improved`, `Unchanged`, `Regressed`, or `Inconclusive`. Selection is an explicit operator action and preserves both revisions.
