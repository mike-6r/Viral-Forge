# Corrective render limitations

The current safe implementation supports bounded timing changes, loudness normalization, peak limiting, and bounded gain only. It deliberately does not implement arbitrary FFmpeg commands, user paths, watermark removal, attribution removal, face recognition, voice synthesis, denoising, unsupported crop modes, or simulated subtitle changes.

Every corrective render is separate, private-previewable, re-inspected, and manually selected. A failed render or inspection preserves the original and the revised output for review; neither can publish automatically.
