"""Modular, recorded original-source resolution. It never downloads media or alters it."""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC

from app.common.config import Settings, get_settings
from app.production.source_quality import (
    CandidateSearchProvider,
    MetadataProvider,
    OfficialSourceRegistry,
    QualityResult,
    QualityScoringService,
    SourceMetadata,
    SourceOwnership,
    WatermarkDetectionService,
    WatermarkResult,
    now_iso,
    safe_metadata_json,
)

Runner = Callable[..., subprocess.CompletedProcess[str]]


class YtDlpMetadataProvider:
    def __init__(self, settings: Settings | None = None, runner: Runner = subprocess.run) -> None:
        self.settings = settings or get_settings()
        self.runner = runner

    def extract(self, url: str) -> SourceMetadata:
        from app.production.service import ProductionError, YtDlpDownloadProvider

        command = [
            *YtDlpDownloadProvider(self.settings).command_prefix(),
            "--dump-single-json",
            "--skip-download",
            "--no-playlist",
            "--no-cookies",
            "--no-warnings",
            url,
        ]
        try:
            result = self.runner(
                command,
                capture_output=True,
                text=True,
                timeout=self.settings.source_search_timeout_seconds,
                check=True,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ProductionError(
                "SOURCE_METADATA_FAILED", "source metadata could not be resolved"
            ) from error
        payload = safe_metadata_json(result.stdout)
        if not payload:
            raise ProductionError("SOURCE_METADATA_FAILED", "source metadata could not be resolved")
        fps = payload.get("fps")
        duration = payload.get("duration")
        width = payload.get("width")
        height = payload.get("height")
        tbr = payload.get("tbr")
        file_size = payload.get("filesize_approx")
        views = payload.get("view_count")
        return SourceMetadata(
            platform=str(payload.get("extractor_key", "YOUTUBE")).upper(),
            source_url=url,
            uploader_name=str(payload["uploader"])[:500] if payload.get("uploader") else None,
            uploader_account_id=str(payload["channel_id"])[:255]
            if payload.get("channel_id")
            else None,
            account_url=str(payload["channel_url"])[:2048] if payload.get("channel_url") else None,
            video_title=str(payload["title"])[:500] if payload.get("title") else None,
            description=str(payload["description"]) if payload.get("description") else None,
            upload_date=str(payload["upload_date"])[:32] if payload.get("upload_date") else None,
            duration_seconds=float(duration) if isinstance(duration, int | float) else None,
            width=int(width) if isinstance(width, int | float) else None,
            height=int(height) if isinstance(height, int | float) else None,
            frame_rate=float(fps) if isinstance(fps, int | float) else None,
            bitrate=int(tbr * 1000) if isinstance(tbr, int | float) else None,
            file_size_bytes=int(file_size) if isinstance(file_size, int | float) else None,
            view_count=int(views) if isinstance(views, int | float) else None,
            metadata_json={
                "audio_present": bool(payload.get("acodec") and payload.get("acodec") != "none"),
                "extractor": str(payload.get("extractor", "")),
                "id": str(payload.get("id", "")),
            },
        )


class YouTubeCandidateSearchProvider:
    """Official Data API only; no social-platform scraping or crawling."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def search(self, query: str, submitted: SourceMetadata, limit: int) -> list[SourceMetadata]:
        from app.production.youtube import search_youtube

        if not self.settings.youtube_api_key:
            return []
        results = asyncio.run(search_youtube(query, max_results=limit, settings=self.settings))
        candidates: list[SourceMetadata] = []
        for result in results:
            candidates.append(
                SourceMetadata(
                    "YOUTUBE",
                    result.url,
                    result.channel_title,
                    video_title=result.title,
                    upload_date=result.published_at.astimezone(UTC).date().isoformat()
                    if result.published_at
                    else None,
                    view_count=result.view_count,
                    metadata_json={"audio_present": True, "youtube_video_id": result.video_id},
                )
            )
        return candidates


@dataclass(frozen=True)
class ResolvedCandidate:
    metadata: SourceMetadata
    ownership: str
    watermark: WatermarkResult
    quality: QualityResult
    original_confidence: float
    repost_likelihood: float
    reason: str


@dataclass(frozen=True)
class SourceResolution:
    submitted: ResolvedCandidate
    candidates: list[ResolvedCandidate]
    selected_index: int
    needs_manual_review: bool
    query: str

    @property
    def selected(self) -> ResolvedCandidate:
        return self.candidates[self.selected_index]


class OriginalSourceResolver:
    def __init__(
        self,
        settings: Settings | None = None,
        metadata_provider: MetadataProvider | None = None,
        search_provider: CandidateSearchProvider | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.metadata_provider = metadata_provider or YtDlpMetadataProvider(self.settings)
        self.search_provider = search_provider or YouTubeCandidateSearchProvider(self.settings)
        self.registry = OfficialSourceRegistry(self.settings.source_official_registry_path)
        self.watermarks = WatermarkDetectionService(self.settings)
        self.scoring = QualityScoringService(self.settings)

    def query_for(self, metadata: SourceMetadata) -> str:
        words = (metadata.video_title or "") + " " + (metadata.description or "")
        return " ".join(words.split()[:20])

    def _candidate(self, metadata: SourceMetadata, earliest: bool) -> ResolvedCandidate:
        ownership = self.registry.ownership(metadata)
        lowered = f"{metadata.video_title or ''} {metadata.description or ''}".casefold()
        if ownership == SourceOwnership.UNKNOWN and any(
            term in lowered for term in ("repost", "compilation", "screen recording")
        ):
            ownership = SourceOwnership.REPOST_ACCOUNT
        watermark = self.watermarks.detect(metadata, ownership)
        quality = self.scoring.score(metadata, ownership, watermark, earliest)
        original = min(
            1.0,
            (
                0.55
                if ownership in {SourceOwnership.OFFICIAL_AGENCY, SourceOwnership.OFFICIAL_UPLOADER}
                else 0.20
            )
            + quality.score / 200,
        )
        repost = (
            0.75
            if ownership == SourceOwnership.REPOST_ACCOUNT
            else (
                0.55
                if watermark.status == "PLATFORM_WATERMARK"
                else max(0.0, 0.45 - quality.score / 250)
            )
        )
        reason = f"{quality.explanation} Ownership classification: {ownership}."
        return ResolvedCandidate(
            metadata, ownership, watermark, quality, round(original, 2), round(repost, 2), reason
        )

    def resolve(self, submitted_url: str) -> SourceResolution:
        submitted_metadata = self.metadata_provider.extract(submitted_url)
        query = self.query_for(submitted_metadata)
        discovered = (
            self.search_provider.search(
                query, submitted_metadata, self.settings.source_max_candidate_count - 1
            )
            if self.settings.source_resolution_enabled
            else []
        )
        unique: dict[str, SourceMetadata] = {submitted_metadata.source_url: submitted_metadata}
        unique.update({candidate.source_url: candidate for candidate in discovered})
        metadata = list(unique.values())[: self.settings.source_max_candidate_count]
        earliest_date = min(
            (item.upload_date for item in metadata if item.upload_date), default=None
        )
        ranked = [self._candidate(item, item.upload_date == earliest_date) for item in metadata]
        ranked.sort(
            key=lambda candidate: (
                candidate.quality.score,
                candidate.original_confidence,
                -candidate.repost_likelihood,
            ),
            reverse=True,
        )
        submitted = next(
            candidate for candidate in ranked if candidate.metadata.source_url == submitted_url
        )
        needs_manual = any(
            (
                ranked[0].watermark.status in {"PLATFORM_WATERMARK", "MANUAL_REVIEW_REQUIRED"},
                ranked[0].original_confidence < self.settings.source_min_original_confidence,
                ranked[0].quality.score < self.settings.source_min_accepted_quality_score,
                len(ranked) > 1 and abs(ranked[0].quality.score - ranked[1].quality.score) < 5,
            )
        )
        return SourceResolution(submitted, ranked, 0, needs_manual, query)


def discovered_timestamp() -> str:
    return now_iso()
