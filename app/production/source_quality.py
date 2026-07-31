"""Conservative source-quality analysis with no watermark modification capability."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import yaml

from app.common.config import Settings, get_settings


class SourceOwnership:
    OFFICIAL_AGENCY = "OFFICIAL_AGENCY"
    OFFICIAL_UPLOADER = "OFFICIAL_UPLOADER"
    VERIFIED_PARTNER = "VERIFIED_PARTNER"
    NEWS_OR_MEDIA = "NEWS_OR_MEDIA"
    REPOST_ACCOUNT = "REPOST_ACCOUNT"
    UNKNOWN = "UNKNOWN"


class WatermarkStatus:
    NONE_DETECTED = "NONE_DETECTED"
    PLATFORM_WATERMARK = "PLATFORM_WATERMARK"
    UPLOADER_BRANDING = "UPLOADER_BRANDING"
    AGENCY_BRANDING = "AGENCY_BRANDING"
    NEWS_BRANDING = "NEWS_BRANDING"
    UNKNOWN = "UNKNOWN"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"


class SourceQualityStatus:
    ORIGINAL_PREFERRED = "ORIGINAL_PREFERRED"
    ACCEPTABLE = "ACCEPTABLE"
    LOWER_QUALITY = "LOWER_QUALITY"
    REPOST_SUSPECTED = "REPOST_SUSPECTED"
    WATERMARKED_REVIEW = "WATERMARKED_REVIEW"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class SourceMetadata:
    platform: str
    source_url: str
    uploader_name: str | None = None
    uploader_account_id: str | None = None
    account_url: str | None = None
    video_title: str | None = None
    description: str | None = None
    upload_date: str | None = None
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    frame_rate: float | None = None
    bitrate: int | None = None
    file_size_bytes: int | None = None
    view_count: int | None = None
    resolved_media_url: str | None = None
    metadata_json: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class WatermarkResult:
    status: str
    confidence: float
    regions: list[dict[str, float]]
    explanation: str
    sampled_timestamps: list[float]


@dataclass(frozen=True)
class QualityResult:
    score: float
    components: dict[str, float]
    warnings: list[str]
    quality_status: str
    explanation: str


class MetadataProvider(Protocol):
    def extract(self, url: str) -> SourceMetadata: ...


class CandidateSearchProvider(Protocol):
    def search(self, query: str, submitted: SourceMetadata, limit: int) -> list[SourceMetadata]: ...


def _safe_yaml(path: str, fallback: dict[str, object]) -> dict[str, object]:
    try:
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else fallback
    except (OSError, yaml.YAMLError):
        return fallback


def normalize_text(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def source_fingerprint(metadata: SourceMetadata) -> dict[str, str | float | int]:
    title = normalize_text(metadata.video_title)
    summary = "|".join(
        (
            title,
            str(round(metadata.duration_seconds or 0, 1)),
            str(metadata.width or 0),
            str(metadata.height or 0),
        )
    )
    return {
        "metadata_sha256": hashlib.sha256(summary.encode()).hexdigest(),
        "normalized_duration": round(metadata.duration_seconds or 0, 1),
    }


def file_fingerprint(path: Path, metadata: SourceMetadata) -> dict[str, str | float | int]:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(262_144), b""):
            digest.update(chunk)
    result = source_fingerprint(metadata)
    result["file_sha256"] = digest.hexdigest()
    result["file_size_bytes"] = path.stat().st_size
    return result


def fingerprint_similarity(
    left: dict[str, str | float | int], right: dict[str, str | float | int]
) -> float:
    if left.get("file_sha256") and left.get("file_sha256") == right.get("file_sha256"):
        return 1.0
    if left.get("metadata_sha256") == right.get("metadata_sha256"):
        return 0.9
    left_duration, right_duration = (
        float(left.get("normalized_duration", 0)),
        float(right.get("normalized_duration", 0)),
    )
    if left_duration and right_duration:
        return (
            max(0.0, 1 - abs(left_duration - right_duration) / max(left_duration, right_duration))
            * 0.5
        )
    return 0.0


class OfficialSourceRegistry:
    def __init__(self, path: str) -> None:
        self.agencies = _safe_yaml(path, {"agencies": []}).get("agencies", [])

    def ownership(self, metadata: SourceMetadata) -> str:
        account = (metadata.uploader_account_id or "").casefold()
        name = normalize_text(metadata.uploader_name)
        for item in self.agencies if isinstance(self.agencies, list) else []:
            if not isinstance(item, dict) or not item.get("enabled"):
                continue
            ids = [str(value).casefold() for value in item.get("youtube_channels", [])]
            aliases = [
                normalize_text(str(value))
                for value in [item.get("name", ""), *item.get("aliases", [])]
            ]
            if account in ids or (name and name in aliases):
                return SourceOwnership.OFFICIAL_AGENCY
        labels = f"{metadata.uploader_name or ''} {metadata.video_title or ''}".casefold()
        if "official" in labels:
            return SourceOwnership.OFFICIAL_UPLOADER
        if any(word in labels for word in ("news", "media", "broadcast")):
            return SourceOwnership.NEWS_OR_MEDIA
        return SourceOwnership.UNKNOWN


class WatermarkDetectionService:
    """A deliberately conservative detector; it reports overlays and never changes pixels."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def detect(
        self,
        metadata: SourceMetadata,
        ownership: str,
        sampled_overlays: list[dict[str, object]] | None = None,
    ) -> WatermarkResult:
        lower = " ".join(
            str(value)
            for value in (
                metadata.video_title,
                metadata.description,
                metadata.metadata_json.get("watermark_hint", ""),
            )
        ).casefold()
        timestamps = [
            round(
                index
                * max(metadata.duration_seconds or 0, 1)
                / self.settings.source_sampled_watermark_frame_count,
                2,
            )
            for index in range(self.settings.source_sampled_watermark_frame_count)
        ]
        if ownership == SourceOwnership.OFFICIAL_AGENCY and any(
            word in lower for word in ("body cam", "bodycam", "axon", "department", "evidence")
        ):
            return WatermarkResult(
                WatermarkStatus.AGENCY_BRANDING,
                0.75,
                [],
                "Official evidence or agency overlay retained unchanged.",
                timestamps,
            )
        if "tiktok watermark" in lower or "platform watermark" in lower:
            return WatermarkResult(
                WatermarkStatus.PLATFORM_WATERMARK,
                0.80,
                [{"x": 0.80, "y": 0.80, "width": 0.20, "height": 0.20}],
                "Metadata or sampled-frame signal indicates a platform watermark.",
                timestamps,
            )
        if "watermark" in lower or "repost" in lower:
            return WatermarkResult(
                WatermarkStatus.MANUAL_REVIEW_REQUIRED,
                0.55,
                [],
                "Possible overlay detected; a reviewer must decide.",
                timestamps,
            )
        if sampled_overlays:

            def is_stable(entry: dict[str, object]) -> bool:
                stability = entry.get("stability")
                return isinstance(stability, int | float) and float(stability) >= 0.85

            stable = [entry for entry in sampled_overlays if is_stable(entry)]
            if stable:
                return WatermarkResult(
                    WatermarkStatus.MANUAL_REVIEW_REQUIRED,
                    0.55,
                    [],
                    "Static overlay pattern requires human review.",
                    timestamps,
                )
        return WatermarkResult(
            WatermarkStatus.UNKNOWN,
            0.0,
            [],
            "No high-confidence removable-watermark inference made.",
            timestamps,
        )


class QualityScoringService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        config = _safe_yaml(self.settings.source_quality_weights_path, {})
        raw_weights = config.get("weights", {})
        raw_penalties = config.get("penalties", {})
        self.weights: dict[str, float] = (
            {
                str(key): float(value)
                for key, value in raw_weights.items()
                if isinstance(value, int | float)
            }
            if isinstance(raw_weights, dict)
            else {}
        )
        self.penalties: dict[str, float] = (
            {
                str(key): float(value)
                for key, value in raw_penalties.items()
                if isinstance(value, int | float)
            }
            if isinstance(raw_penalties, dict)
            else {}
        )

    def score(
        self,
        metadata: SourceMetadata,
        ownership: str,
        watermark: WatermarkResult,
        earliest: bool = False,
    ) -> QualityResult:
        def weight(key: str, default: float) -> float:
            return self.weights.get(key, default)

        def penalty(key: str, default: float) -> float:
            return self.penalties.get(key, default)

        components: dict[str, float] = {}
        components["official_uploader"] = (
            weight("official_uploader", 30)
            if ownership in {SourceOwnership.OFFICIAL_AGENCY, SourceOwnership.OFFICIAL_UPLOADER}
            else 0
        )
        components["original_upload"] = (
            weight("original_upload", 18) if ownership != SourceOwnership.REPOST_ACCOUNT else 0
        )
        components["resolution"] = (
            weight("resolution", 15)
            if (metadata.width or 0) >= self.settings.source_preferred_min_width
            else 0
        )
        components["bitrate"] = weight("bitrate", 8) if (metadata.bitrate or 0) >= 2_000_000 else 0
        components["frame_rate"] = (
            weight("frame_rate", 5) if (metadata.frame_rate or 0) >= 24 else 0
        )
        components["completeness"] = (
            weight("completeness", 10) if (metadata.duration_seconds or 0) > 30 else 0
        )
        components["audio_present"] = (
            weight("audio_present", 4)
            if metadata.metadata_json.get("audio_present") is not False
            else 0
        )
        preferred = {
            item.strip().upper() for item in self.settings.source_preferred_platforms.split(",")
        }
        components["preferred_platform"] = (
            weight("preferred_platform", 3) if metadata.platform.upper() in preferred else 0
        )
        components["earliest_upload"] = weight("earliest_upload", 7) if earliest else 0
        warnings: list[str] = []
        deductions = 0.0
        if watermark.status == WatermarkStatus.PLATFORM_WATERMARK:
            deductions += penalty("platform_watermark", 25)
            warnings.append("Platform watermark detected; manual review required.")
        elif watermark.status in {
            WatermarkStatus.MANUAL_REVIEW_REQUIRED,
            WatermarkStatus.UPLOADER_BRANDING,
        }:
            deductions += penalty("unrelated_branding", 18)
            warnings.append("Possible unrelated uploader branding; manual review required.")
        if ownership == SourceOwnership.REPOST_ACCOUNT:
            deductions += penalty("repost_likelihood", 20)
            warnings.append("Candidate appears to be a repost account.")
        if metadata.width and metadata.height and metadata.height > metadata.width:
            deductions += penalty("cropped_or_vertical", 8)
            warnings.append("Vertical source may be a cropped or platform-native copy.")
        score = round(max(0.0, min(100.0, sum(components.values()) - deductions)), 2)
        if watermark.status in {
            WatermarkStatus.PLATFORM_WATERMARK,
            WatermarkStatus.MANUAL_REVIEW_REQUIRED,
        }:
            quality_status = SourceQualityStatus.WATERMARKED_REVIEW
        elif ownership == SourceOwnership.REPOST_ACCOUNT:
            quality_status = SourceQualityStatus.REPOST_SUSPECTED
        elif score < self.settings.source_min_accepted_quality_score:
            quality_status = SourceQualityStatus.LOWER_QUALITY
        elif ownership in {SourceOwnership.OFFICIAL_AGENCY, SourceOwnership.OFFICIAL_UPLOADER}:
            quality_status = SourceQualityStatus.ORIGINAL_PREFERRED
        else:
            quality_status = SourceQualityStatus.ACCEPTABLE
        return QualityResult(
            score,
            components,
            warnings,
            quality_status,
            f"Score {score:.0f}: ownership, media attributes, upload timing, and overlay risk were weighed together.",
        )


def metadata_payload(metadata: SourceMetadata) -> dict[str, object]:
    """Strip credential-shaped fields before persistence or audit logging."""
    blocked = ("cookie", "token", "authorization", "password", "signature", "signed_url")
    return {
        key: value
        for key, value in metadata.metadata_json.items()
        if not any(term in key.casefold() for term in blocked)
    }


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def safe_metadata_json(value: str) -> dict[str, object]:
    try:
        payload = json.loads(value)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        return {}
