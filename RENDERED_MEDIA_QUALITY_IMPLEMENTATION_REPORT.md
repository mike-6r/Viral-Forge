# Rendered Media Quality Intelligence

ViralForge now has a versioned, brand-scoped, advisory inspection record for a successfully rendered authoritative clip. The inspection copies the durable rendered asset into an isolated temporary directory, uses bounded FFprobe/FFmpeg calls, persists measurements and immutable issue rows, then removes the temporary media.

It records technical stream evidence, aspect ratio, sampled-frame black/low-activity evidence, audio stream/duration/peak evidence, conservative subtitle and safe-area limitations, hook-opening evidence, confidence, and an operator decision version. It never inspects a preview proxy and never changes a clip's approval, render, queue, schedule, upload, or publishing state.

`ClipQualityReport` now records completed rendered-inspection evidence when available. Its subtitle score remains explicitly distinguished from transcript-coverage evidence.

The new worker tasks are `viralforge.inspect_rendered_media` and `viralforge.cleanup_rendered_inspection_temp`. Automatic inspection after rendering is disabled by default and is enabled only with a Brand ContentProfile configuration. A failure is fail-open: normal finished-clip review continues.
