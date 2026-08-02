# Rendered Media Quality configuration

Global safe defaults are local and bounded:

- `VIRALFORGE_RENDERED_MEDIA_INSPECTION_ENABLED=true`
- `VIRALFORGE_RENDERED_MEDIA_INSPECTION_AUTO_RUN=false`
- `VIRALFORGE_RENDERED_MEDIA_INSPECTION_PROVIDER=local_ffmpeg`
- `VIRALFORGE_RENDERED_MEDIA_INSPECTION_MAX_SAMPLES=30`
- `VIRALFORGE_RENDERED_MEDIA_INSPECTION_TIMEOUT_SECONDS=300`
- `VIRALFORGE_RENDERED_MEDIA_INSPECTION_OCR_ENABLED=false`
- `VIRALFORGE_RENDERED_MEDIA_INSPECTION_CV_ENABLED=false`

Per-brand overrides belong in `ContentProfile.rendered_media_inspection_json`. Supported keys include `enabled`, `auto_run`, `safe_area_profile`, `sampling_interval_seconds`, `max_samples`, `timeout_seconds`, `ocr_enabled`, `audio_checks_enabled`, `subtitle_checks_enabled`, `hook_checks_enabled`, and `minimum_readiness_score`.

Safe-area profiles are versioned configuration in `config/rendered_media_safe_areas.yml`: `generic_9_16`, `youtube_shorts`, `tiktok`, `instagram_reels`, and `facebook_reels`. Defining these profiles does not enable a provider, account connection, transfer, or publishing.
