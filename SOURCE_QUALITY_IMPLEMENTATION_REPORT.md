# Source Quality, Original-Source Resolution, and Watermark Review

## Outcome

ViralForge now records the submitted public source and every considered candidate before download. It ranks candidates conservatively, selects the strongest recorded source, and routes uncertain, reposted, or watermarked sources to manual review. The existing download, clip generation, clip approval, and posting-queue flow remains unchanged after a source is accepted or automatically resolved.

No watermark removal, concealment, blurring, cropping for watermark purposes, or attribution modification functionality was added.

## Architecture

`app.production.source_resolver.OriginalSourceResolver` composes a metadata provider, candidate-search provider, official-source registry, watermark detector, and quality scorer. The default metadata provider uses `yt-dlp --dump-single-json --skip-download --no-cookies`; it does not download at this stage. Candidate discovery uses the existing official YouTube Data API boundary only when configured. Tests inject mocked providers, so automated tests never contact a social platform.

`ProductionSource` stores submitted and candidate records. Candidate records point to the submitted record through `parent_source_id`; `ProductionProject.selected_source_id` is the canonical selected source. Metadata is allowlisted and credential-shaped keys such as cookies, tokens, signatures, and authorization values are excluded before persistence and audit logging.

## Migration

`0010_source_quality_review` creates `production_sources`, links projects to a selected source, and adds `source_decision_version` for stale-action protection. PostgreSQL receives the selected-source foreign key; SQLite’s disposable migration test omits that alter-only constraint because SQLite does not support it, while still validating full ORM column parity.

## Scoring and classifications

The classifications are persisted as strings:

- Ownership: `OFFICIAL_AGENCY`, `OFFICIAL_UPLOADER`, `VERIFIED_PARTNER`, `NEWS_OR_MEDIA`, `REPOST_ACCOUNT`, `UNKNOWN`.
- Watermark: `NONE_DETECTED`, `PLATFORM_WATERMARK`, `UPLOADER_BRANDING`, `AGENCY_BRANDING`, `NEWS_BRANDING`, `UNKNOWN`, `MANUAL_REVIEW_REQUIRED`.
- Source quality: `ORIGINAL_PREFERRED`, `ACCEPTABLE`, `LOWER_QUALITY`, `REPOST_SUSPECTED`, `WATERMARKED_REVIEW`, `REJECTED`.

`config/source_quality_weights.yml` controls quality weights and penalties. The score considers ownership, upload timing, resolution, bitrate, frame rate, duration/completeness, audio availability, platform preference, repost likelihood, vertical/crop risk, and watermark risk. The result includes component scores, warnings, and an explanation. A high-resolution repost does not outrank a verified official candidate without the score and recorded reason reflecting that decision.

## Official-source registry

`config/official_sources.yml` is a configurable registry with clearly disabled example data. Agencies, aliases, public account IDs, jurisdiction, public URLs, verification status, and enabled state remain configuration—not application code.

## Watermark and fingerprint review

`WatermarkDetectionService` is intentionally conservative. It can report known platform-watermark hints and repeated static-overlay signals, but low-confidence results are sent to review. Official body-camera overlays, Axon labels, department/evidence labels, timestamps, and official agency graphics are classified as retained agency branding, never as something to remove.

Fingerprinting records normalized duration and metadata hashes before download, plus file SHA-256 and file size after download. Similarity is a ranking and review signal only; it never blocks processing.

## Download and Discord review flow

1. A URL submission creates the project and records the submitted source.
2. Metadata and configured-source candidates are resolved and ranked.
3. The selected source is retained with its reason; all candidates remain queryable through the API.
4. If the selected result is unclear, watermarked, repost-suspected, low-confidence, materially low quality, or too close to another candidate, the project enters `SOURCE_REVIEW_REQUIRED`.
5. The existing Discord dashboard displays platform, uploader, ownership, score, original confidence, repost risk, watermark status, resolution/duration when available, reason, and warnings.
6. Authorized users can accept/reject a source, view the source URL/candidate list, or choose another candidate. Candidate selection carries the project decision version, rejects stale selections, records an audit event, and cannot replace a source after a download or clips exist.
7. Download then uses the selected candidate URL and continues through the unchanged clipping pipeline.

The API exposes source candidates and source accept/reject/select endpoints under `/api/v1/production/projects/{project_id}/sources` and `/source/*`.

## Configuration

`.env.example` now documents all source-resolution thresholds: enablement, candidate count, search timeout, original confidence, accepted score, watermark-review threshold, duplicate threshold, preferred resolution/platforms, registry/weights paths, cache duration, and sampled-frame count. Existing `.env` values were not overwritten.

## Verification

- Mocked source-resolution demonstration: `tests/test_source_quality.py`.
- Full test suite: 70 passed.
- Ruff: passed.
- mypy: passed for 49 modules.
- Migration upgrade/downgrade/re-upgrade and schema parity: passed.
- PostgreSQL migration: upgraded to `0010_source_quality_review`.
- Rebuilt Docker images; API health and readiness passed, Redis/PostgreSQL remained healthy, and the Discord bot reconnected to the Gateway.

## Limitations

- This milestone deliberately does not scrape TikTok, Instagram, Facebook, or X, crawl the web, publish content, or remove watermarks.
- Actual visual watermark recognition remains conservative without OCR/computer-vision dependencies; ambiguous overlays are reviewed rather than treated as removable.
- Candidate discovery uses YouTube’s official API only when `YOUTUBE_API_KEY` is configured. Without it, the submitted source remains recorded and is manually reviewed if confidence is insufficient.

## Files changed

- `app/common/config.py`, `app/production/models.py`, `app/production/service.py`, `app/production/source_quality.py`, `app/production/source_resolver.py`, `app/api.py`, `app/discord_bot.py`
- `alembic/versions/0010_source_quality_review.py`
- `config/official_sources.yml`, `config/source_quality_weights.yml`
- `Dockerfile`, `pyproject.toml`, `.env.example`
- `tests/test_source_quality.py`

## Next recommended milestone

Add reviewer-assisted support for additional official public-source providers using their approved APIs, then improve watermark detection evidence with opt-in local frame/OCR analysis. Keep all source changes auditable and retain the current human acceptance gate.
