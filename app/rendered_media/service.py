"""Bounded, local inspection of the authoritative rendered MP4.

This module intentionally produces advisory evidence only.  It neither changes
the rendered media nor advances approvals, queues, schedules, uploads, or posts.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.brands.models import ContentProfile
from app.common.config import Settings, get_settings
from app.ingestion.storage import LocalFilesystemStorage
from app.media_preview.service import PreviewError, ensure_clip_asset
from app.production.models import ProductionClip
from app.production.service import ProductionError
from app.rendered_media.models import (
    RenderedMediaInspection,
    RenderedMediaInspectionIssue,
    RenderedMediaInspectionReviewStatus,
    RenderedMediaInspectionStatus,
)

Runner = Callable[..., Any]
_TERMINAL = {
    RenderedMediaInspectionStatus.COMPLETED,
    RenderedMediaInspectionStatus.FAILED,
    RenderedMediaInspectionStatus.CANCELLED,
    RenderedMediaInspectionStatus.STALE,
}


class RenderedMediaInspectionError(ProductionError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _bounded(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 2)


def _int(value: object) -> int:
    return int(cast(Any, value))


def _float(value: object) -> float:
    return float(cast(Any, value))


def _safe_profiles() -> dict[str, dict[str, object]]:
    path = Path(__file__).parents[2] / "config" / "rendered_media_safe_areas.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return dict(data.get("profiles") or {})


def inspection_config(session: Session, clip: ProductionClip, settings: Settings | None = None) -> dict[str, object]:
    """Merge safe global defaults with a brand's non-secret profile settings."""
    settings = settings or get_settings()
    profile = session.scalar(select(ContentProfile).where(ContentProfile.brand_id == clip.brand_id))
    profile_data = (profile.rendered_media_inspection_json if profile else {}) or {}
    config: dict[str, object] = {
        "enabled": settings.rendered_media_inspection_enabled,
        "auto_run": settings.rendered_media_inspection_auto_run,
        "provider": settings.rendered_media_inspection_provider,
        "safe_area_profile": settings.rendered_media_inspection_safe_area_profile,
        "sampling_interval_seconds": settings.rendered_media_inspection_sampling_interval_seconds,
        "max_samples": settings.rendered_media_inspection_max_samples,
        "timeout_seconds": settings.rendered_media_inspection_timeout_seconds,
        "ocr_enabled": settings.rendered_media_inspection_ocr_enabled,
        "cv_enabled": settings.rendered_media_inspection_cv_enabled,
        "subtitle_checks_enabled": True,
        "audio_checks_enabled": True,
        "sync_checks_enabled": True,
        "hook_checks_enabled": True,
        "minimum_readiness_score": 65.0,
        "fail_open": True,
    }
    config.update({key: value for key, value in profile_data.items() if key in config})
    config["max_samples"] = max(4, min(120, _int(config["max_samples"])))
    config["timeout_seconds"] = max(10, min(3600, _int(config["timeout_seconds"])))
    config["sampling_interval_seconds"] = max(0.1, min(60.0, _float(config["sampling_interval_seconds"])))
    profiles = _safe_profiles()
    if str(config["safe_area_profile"]) not in profiles:
        config["safe_area_profile"] = "generic_9_16"
    if str(config["provider"]) != "local_ffmpeg":
        # Provider protocols may be introduced later, but external CV is never
        # silently selected in this locally-verifiable foundation.
        config["provider"] = "local_ffmpeg"
    return config


def _audit(session: Session, actor_id: uuid.UUID | None, item: RenderedMediaInspection, event: str, **payload: object) -> None:
    session.add(AuditEvent(
        actor_id=actor_id, entity_type="rendered_media_inspection", entity_id=item.id,
        brand_id=item.brand_id, event_name=event, payload={"pipeline_changed": False, **payload},
    ))


def _latest(session: Session, clip_id: uuid.UUID) -> RenderedMediaInspection | None:
    return session.scalar(select(RenderedMediaInspection).where(RenderedMediaInspection.clip_id == clip_id).order_by(RenderedMediaInspection.inspection_version.desc()))


def request_inspection(
    session: Session,
    actor_id: uuid.UUID | None,
    clip: ProductionClip,
    storage: LocalFilesystemStorage,
    *,
    rerun: bool = False,
    settings: Settings | None = None,
) -> RenderedMediaInspection:
    """Queue (but do not execute) one inspection, reusing an equivalent record."""
    if clip.render_status != "SUCCEEDED" or not clip.storage_key:
        raise RenderedMediaInspectionError("CLIP_NOT_RENDERED", "a successfully rendered authoritative clip is required")
    config = inspection_config(session, clip, settings)
    if not bool(config["enabled"]):
        raise RenderedMediaInspectionError("RENDERED_MEDIA_INSPECTION_DISABLED", "inspection is disabled for this brand")
    try:
        asset = ensure_clip_asset(session, clip, storage, settings)
    except PreviewError as error:
        raise RenderedMediaInspectionError(error.code, str(error)) from error
    previous = _latest(session, clip.id)
    if previous and previous.media_asset_id == asset.id and not rerun and previous.status in {
        RenderedMediaInspectionStatus.QUEUED,
        RenderedMediaInspectionStatus.RUNNING,
        RenderedMediaInspectionStatus.COMPLETED,
    }:
        return previous
    if previous and previous.status not in _TERMINAL:
        previous.status = RenderedMediaInspectionStatus.STALE
        previous.current_stage = "STALE"
    elif previous and previous.media_asset_id != asset.id:
        previous.status = RenderedMediaInspectionStatus.STALE
        previous.current_stage = "STALE"
    item = RenderedMediaInspection(
        brand_id=clip.brand_id, project_id=clip.project_id, clip_id=clip.id, media_asset_id=asset.id,
        inspection_version=(previous.inspection_version + 1) if previous else 1,
        provider=str(config["provider"]), provider_version="local-v1",
        safe_area_profile=str(config["safe_area_profile"]),
        evidence_json={"configuration": {key: config[key] for key in ("safe_area_profile", "max_samples", "sampling_interval_seconds", "ocr_enabled", "cv_enabled")}},
    )
    session.add(item)
    session.flush()
    _audit(session, actor_id, item, "rendered_media.inspection.queued", rerun=rerun)
    session.commit()
    return item


def _stage(session: Session, item: RenderedMediaInspection, name: str, progress: float) -> bool:
    session.refresh(item)
    if item.status == RenderedMediaInspectionStatus.CANCELLED:
        return False
    item.current_stage = name
    item.progress_percent = progress
    session.commit()
    return True


def _run(runner: Runner, command: list[str], timeout: int, *, text: bool = True) -> Any:
    try:
        result = runner(command, capture_output=True, text=text, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RenderedMediaInspectionError("INSPECTION_TOOL_FAILED", "local media inspection tool did not complete") from error
    if result.returncode != 0:
        raise RenderedMediaInspectionError("INSPECTION_MEDIA_UNREADABLE", "authoritative media could not be inspected")
    return result


def _probe(runner: Runner, settings: Settings, path: Path, timeout: int) -> dict[str, object]:
    result = _run(runner, [settings.ffprobe_path, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], timeout)
    try:
        data = json.loads(result.stdout)
        if not isinstance(data, dict):
            raise TypeError("FFprobe payload is not an object")
        return cast(dict[str, object], data)
    except (TypeError, json.JSONDecodeError) as error:
        raise RenderedMediaInspectionError("INSPECTION_INVALID_PROBE", "FFprobe did not return readable media metadata") from error


def _sample_times(duration: float, interval: float, maximum: int) -> list[float]:
    fixed = [0.0, 0.5, 1.0, 2.0, max(0.0, duration - 2.0), max(0.0, duration - 1.0), max(0.0, duration - 0.1)]
    cursor = 3.0
    while cursor < max(3.0, duration - 2.0):
        fixed.append(cursor)
        cursor += interval
    return sorted({round(min(max(0.0, point), max(0.0, duration - 0.01)), 3) for point in fixed})[:maximum]


def _frame_stats(raw: bytes) -> tuple[float, float]:
    if not raw:
        raise RenderedMediaInspectionError("INSPECTION_FRAME_DECODE_FAILED", "a sampled rendered frame could not be decoded")
    values = list(raw)
    mean = sum(values) / len(values)
    spread = max(values) - min(values)
    return mean, float(spread)


def _issue(item: RenderedMediaInspection, issue_type: str, severity: str, explanation: str, recommendation: str, *, start: float | None = None, end: float | None = None, frame_index: int | None = None, measured: dict[str, object] | None = None, expected: dict[str, object] | None = None, confidence: float = 0.7) -> RenderedMediaInspectionIssue:
    return RenderedMediaInspectionIssue(
        inspection_id=item.id, issue_type=issue_type, severity=severity, start_seconds=start, end_seconds=end,
        frame_index=frame_index, explanation=explanation, recommendation=recommendation,
        measured_value_json=measured or {}, expected_range_json=expected or {},
        evidence_json={"source": "local_ffmpeg_sample"}, confidence=confidence,
    )


def _streams(probe: dict[str, object]) -> tuple[dict[str, object] | None, dict[str, object] | None, float]:
    raw_streams = probe.get("streams") or []
    streams = [cast(dict[str, object], stream) for stream in cast(list[object], raw_streams) if isinstance(stream, dict)]
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    raw_format = probe.get("format") or {}
    fmt = cast(dict[str, object], raw_format) if isinstance(raw_format, dict) else {}
    try:
        duration = _float(fmt.get("duration") or (video or {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return video, audio, duration


def execute_inspection(
    session: Session,
    actor_id: uuid.UUID | None,
    item: RenderedMediaInspection,
    storage: LocalFilesystemStorage,
    *,
    settings: Settings | None = None,
    runner: Runner = subprocess.run,
) -> RenderedMediaInspection:
    """Run one bounded local inspection.  Failures remain advisory and persisted."""
    settings = settings or get_settings()
    if item.status in _TERMINAL | {RenderedMediaInspectionStatus.RUNNING}:
        return item
    clip = session.get(ProductionClip, item.clip_id)
    if clip is None or not clip.storage_key or clip.render_status != "SUCCEEDED":
        item.status = RenderedMediaInspectionStatus.FAILED
        item.current_stage = "FAILED"
        item.failure_category = "AUTHORITATIVE_MEDIA_UNAVAILABLE"
        item.failed_at = _now()
        item.summary = "The authoritative rendered clip is unavailable; no preview proxy was inspected."
        session.commit()
        return item
    asset = ensure_clip_asset(session, clip, storage, settings)
    if item.media_asset_id != asset.id or asset.storage_key != clip.storage_key:
        item.status = RenderedMediaInspectionStatus.STALE
        item.current_stage = "STALE"
        item.summary = "A newer authoritative rendered asset replaced this inspection target."
        session.commit()
        return item
    config = inspection_config(session, clip, settings)
    timeout = _int(config["timeout_seconds"])
    was_held = asset.administrative_hold
    item.status = RenderedMediaInspectionStatus.RUNNING
    item.current_stage = "VALIDATING_MEDIA"
    item.progress_percent = 5.0
    item.started_at = _now()
    asset.administrative_hold = True
    session.commit()
    temp_root = Path(settings.video_work_root) / "rendered-inspections"
    temp_root.mkdir(parents=True, exist_ok=True)
    issues: list[RenderedMediaInspectionIssue] = []
    try:
        with tempfile.TemporaryDirectory(prefix="vf-rendered-inspection-", dir=temp_root) as directory:
            media_path = Path(directory) / "authoritative.mp4"
            with storage.open(asset.storage_key) as source, media_path.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            probe = _probe(runner, settings, media_path, timeout)
            video, audio, duration = _streams(probe)
            if video is None:
                raise RenderedMediaInspectionError("INSPECTION_NO_VIDEO", "authoritative media has no video stream")
            if duration <= 0:
                raise RenderedMediaInspectionError("INSPECTION_INVALID_DURATION", "authoritative media has no positive duration")
            width, height = _int(video.get("width") or 0), _int(video.get("height") or 0)
            if width <= 0 or height <= 0:
                raise RenderedMediaInspectionError("INSPECTION_INVALID_RESOLUTION", "authoritative media has an invalid resolution")
            if audio is None:
                issues.append(_issue(item, "MISSING_AUDIO", "HIGH", "No audio stream was found in the rendered asset.", "Review the render and source audio before using this clip.", confidence=0.98))
            elif audio.get("duration") and abs(_float(audio["duration"]) - duration) > 0.35:
                issues.append(_issue(item, "AUDIO_VIDEO_DURATION_MISMATCH", "MEDIUM", "Audio and video durations differ beyond the local threshold.", "Review the clip start and ending synchronization.", measured={"seconds": round(abs(_float(audio["duration"]) - duration), 3)}, expected={"maximum_seconds": 0.35}, confidence=0.85))
            ratio = width / height
            expected_ratio = _float(_safe_profiles()[str(config["safe_area_profile"])].get("aspect_ratio") or 0.5625)
            if abs(ratio - expected_ratio) > 0.03:
                issues.append(_issue(item, "ASPECT_RATIO", "MEDIUM", "Rendered aspect ratio differs from the selected safe-area profile.", "Review crop framing for the intended destination.", measured={"ratio": round(ratio, 4)}, expected={"ratio": expected_ratio}, confidence=0.95))
            if not _stage(session, item, "SAMPLING_FRAMES", 25):
                return item
            samples: list[dict[str, float]] = []
            for frame_index, timestamp in enumerate(_sample_times(duration, _float(config["sampling_interval_seconds"]), _int(config["max_samples"]))):
                result = _run(runner, [settings.ffmpeg_path, "-v", "error", "-ss", str(timestamp), "-i", str(media_path), "-frames:v", "1", "-vf", "scale=64:36,format=gray", "-f", "rawvideo", "pipe:1"], min(20, timeout), text=False)
                raw = result.stdout if isinstance(result.stdout, bytes) else str(result.stdout).encode()
                mean, spread = _frame_stats(raw)
                samples.append({"time": timestamp, "mean": round(mean, 2), "spread": round(spread, 2)})
                if mean < 8 and (timestamp < 3 or timestamp > duration - 2):
                    issues.append(_issue(item, "BLACK_FRAME", "MEDIUM", "A near-black frame appears at the opening or ending.", "Review opening/ending timing before use.", start=timestamp, end=timestamp, frame_index=frame_index, measured={"luma_mean": round(mean, 2)}, expected={"minimum_luma_mean": 8}, confidence=0.9))
            for before, after in zip(samples, samples[1:], strict=False):
                if abs(before["mean"] - after["mean"]) < 0.2 and before["spread"] < 3 and after["spread"] < 3:
                    issues.append(_issue(item, "LOW_VISUAL_ACTIVITY", "LOW", "Two sampled frames appear nearly unchanged and low-detail.", "Confirm this is intentional rather than a frozen or blank section.", start=before["time"], end=after["time"], measured={"luma_change": abs(before["mean"] - after["mean"])}, confidence=0.45))
                    break
            if not _stage(session, item, "ANALYZING_AUDIO", 60):
                return item
            audio_metrics: dict[str, object] = {}
            if audio is not None and bool(config["audio_checks_enabled"]):
                result = _run(runner, [settings.ffmpeg_path, "-v", "info", "-i", str(media_path), "-af", "volumedetect", "-f", "null", "-"], min(30, timeout))
                audio_metrics["volumedetect"] = str(result.stderr)[-1000:]
                if "max_volume: 0.0 dB" in str(result.stderr):
                    issues.append(_issue(item, "POSSIBLE_AUDIO_CLIPPING", "MEDIUM", "The measured peak reaches 0 dB.", "Listen for clipping before publishing.", confidence=0.7))
            if not _stage(session, item, "ANALYZING_SUBTITLES", 75):
                return item
            transcript_note = "Visual burned-in subtitle OCR is disabled; subtitle readability and timing remain operator-review items."
            if bool(config["ocr_enabled"]):
                transcript_note = "OCR is configured but local OCR comparison is intentionally bounded and advisory."
            issues.append(_issue(item, "SUBTITLE_VISUAL_LIMITATION", "INFO", transcript_note, "Use the preview to verify subtitle position, contrast, and timing.", confidence=0.25))
            if not _stage(session, item, "GENERATING_REPORT", 90):
                return item
            severe = sum(1 for issue in issues if issue.severity in {"HIGH", "CRITICAL"})
            medium = sum(1 for issue in issues if issue.severity == "MEDIUM")
            technical = _bounded(100 - severe * 28 - medium * 10)
            visual = _bounded(92 - sum(1 for issue in issues if issue.issue_type in {"BLACK_FRAME", "LOW_VISUAL_ACTIVITY", "ASPECT_RATIO"}) * 12)
            framing = _bounded(95 - (18 if abs(ratio - expected_ratio) > 0.03 else 0))
            safe_area = 55.0
            subtitle = 45.0
            audio_score = _bounded(90 - (35 if audio is None else 0) - (12 if any(issue.issue_type == "POSSIBLE_AUDIO_CLIPPING" for issue in issues) else 0))
            hook = _bounded(82 - (20 if any(issue.issue_type == "BLACK_FRAME" and (issue.start_seconds or 0) < 3 for issue in issues) else 0))
            overall = _bounded(technical * .24 + visual * .16 + framing * .12 + safe_area * .10 + subtitle * .10 + audio_score * .16 + hook * .12)
            item.technical_score, item.visual_score, item.framing_score = technical, visual, framing
            item.safe_area_score, item.subtitle_score, item.audio_score, item.hook_score = safe_area, subtitle, audio_score, hook
            item.overall_score, item.confidence = overall, 0.72 if audio is not None else 0.65
            item.evidence_json = {**item.evidence_json, "technical": {"duration_seconds": round(duration, 3), "width": width, "height": height, "aspect_ratio": round(ratio, 4), "video_codec": str(video.get("codec_name") or "unknown"), "audio_codec": str((audio or {}).get("codec_name") or "missing")}, "sampling": {"count": len(samples), "samples": samples, "frames_persisted": False}, "audio": audio_metrics, "limitations": ["No face recognition or identity detection.", "No extracted frame images are retained.", transcript_note]}
            item.warnings_json = [issue.issue_type for issue in issues if issue.severity in {"HIGH", "MEDIUM"}]
            item.summary = f"Local advisory inspection completed with overall readiness {overall:.0f}/100 and {len(item.warnings_json)} material warning(s)."
            item.status, item.current_stage, item.progress_percent, item.completed_at = RenderedMediaInspectionStatus.COMPLETED, "COMPLETED", 100.0, _now()
            session.add_all(issues)
            _audit(session, actor_id, item, "rendered_media.inspection.completed", issue_count=len(issues), overall_score=overall)
            session.commit()
            return item
    except RenderedMediaInspectionError as error:
        item.status, item.current_stage, item.failure_category, item.failed_at = RenderedMediaInspectionStatus.FAILED, "FAILED", error.code, _now()
        item.summary = "Rendered-media inspection could not complete; the clip remains available for normal review."
        _audit(session, actor_id, item, "rendered_media.inspection.failed", failure_category=error.code)
        session.commit()
        return item
    finally:
        if not was_held:
            asset.administrative_hold = False
            session.commit()


def cancel_inspection(session: Session, actor_id: uuid.UUID, item: RenderedMediaInspection) -> RenderedMediaInspection:
    if item.status in _TERMINAL:
        return item
    item.status, item.current_stage = RenderedMediaInspectionStatus.CANCELLED, "CANCELLED"
    _audit(session, actor_id, item, "rendered_media.inspection.cancelled")
    session.commit()
    return item


def review_inspection(session: Session, actor_id: uuid.UUID, item: RenderedMediaInspection, expected_version: int, approved: bool, reason: str | None = None) -> RenderedMediaInspection:
    if item.review_version != expected_version:
        raise RenderedMediaInspectionError("INSPECTION_VERSION_CONFLICT", "inspection advice changed; reload before deciding")
    target = RenderedMediaInspectionReviewStatus.APPROVED if approved else RenderedMediaInspectionReviewStatus.REJECTED
    if item.review_status == target:
        return item
    if item.review_status != RenderedMediaInspectionReviewStatus.PENDING:
        raise RenderedMediaInspectionError("INSPECTION_NOT_REVIEWABLE", "inspection advice already has an operator decision")
    item.review_status, item.review_version, item.decided_by_id, item.decision_reason = target, item.review_version + 1, actor_id, reason
    _audit(session, actor_id, item, "rendered_media.inspection.advice_approved" if approved else "rendered_media.inspection.advice_rejected")
    session.commit()
    return item


def add_operator_note(session: Session, actor_id: uuid.UUID, item: RenderedMediaInspection, expected_version: int, note: str) -> RenderedMediaInspection:
    if item.review_version != expected_version:
        raise RenderedMediaInspectionError("INSPECTION_VERSION_CONFLICT", "inspection advice changed; reload before adding a note")
    item.operator_note, item.review_version = note[:2000], item.review_version + 1
    _audit(session, actor_id, item, "rendered_media.inspection.noted")
    session.commit()
    return item


def cleanup_temporary_inspections(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    root = Path(settings.video_work_root) / "rendered-inspections"
    if not root.exists():
        return 0
    cutoff = _now().timestamp() - settings.rendered_media_inspection_temp_max_age_seconds
    removed = 0
    for path in root.glob("vf-rendered-inspection-*"):
        if path.is_dir() and path.stat().st_mtime < cutoff:
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    return removed
