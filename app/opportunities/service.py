"""Explainable opportunity detection from persisted analysis, never from a second video scan."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.models import (
    AnalysisEvent,
    AnalysisSegment,
    AnalysisStatus,
    TranscriptSegment,
    VideoAnalysis,
)
from app.audit.models import AuditEvent
from app.brands.models import Brand  # noqa: F401
from app.common.config import Settings, get_settings
from app.ingestion.storage import LocalFilesystemStorage
from app.opportunities.models import (
    ClipOpportunity,
    ClipOpportunityVersion,
    OpportunityGenerationRun,
    OpportunityGenerationStatus,
    OpportunityReason,
    OpportunityReviewStatus,
    OpportunityRunStatus,
)
from app.production.models import ProductionClip, ProductionProject
from app.production.service import ProductionError, render_clip_window

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ScoreReason:
    reason_type: str
    score: float
    weight: float
    metadata: dict[str, object]


@dataclass(frozen=True)
class CandidateOpportunity:
    start_time: float
    end_time: float
    overlap_percentage: float


class OpportunityProvider(Protocol):
    def detect(
        self,
        analysis: VideoAnalysis,
        segments: list[AnalysisSegment],
        transcript: list[TranscriptSegment],
        events: list[AnalysisEvent],
    ) -> list[CandidateOpportunity]: ...


def _overlap(start: float, end: float, other_start: float, other_end: float) -> float:
    return max(0.0, min(end, other_end) - max(start, other_start))


def _bounded_window(start: float, end: float, duration: float, settings: Settings) -> tuple[float, float]:
    start = max(0.0, start - settings.opportunity_padding_before_seconds)
    end = min(duration, end + settings.opportunity_padding_after_seconds)
    midpoint = (start + end) / 2
    target_min = float(settings.opportunity_min_duration_seconds)
    target_max = float(settings.opportunity_max_duration_seconds)
    if end - start < target_min:
        start = max(0.0, midpoint - target_min / 2)
        end = min(duration, start + target_min)
        start = max(0.0, end - target_min)
    if end - start > target_max:
        start = max(0.0, midpoint - target_max / 2)
        end = min(duration, start + target_max)
        start = max(0.0, end - target_max)
    return round(start, 3), round(end, 3)


class RuleOpportunityProvider:
    """Config-driven timeline rule provider; future ML/LLM providers share this protocol."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def detect(
        self,
        analysis: VideoAnalysis,
        segments: list[AnalysisSegment],
        transcript: list[TranscriptSegment],
        events: list[AnalysisEvent],
    ) -> list[CandidateOpportunity]:
        if analysis.duration_seconds is None:
            raise ProductionError("ANALYSIS_INCOMPLETE", "analysis has no source duration")
        duration = analysis.duration_seconds
        anchors: list[tuple[float, float]] = []
        for transcript_segment in transcript:
            anchors.append((transcript_segment.start_time, transcript_segment.end_time))
        for analysis_segment in segments:
            if analysis_segment.segment_type in {
                "SPEECH",
                "SCENE",
                "SHOT_CHANGE",
                "MOTION",
                "HIGH_MOTION",
                "LOUD_AUDIO",
            }:
                anchors.append((analysis_segment.start_time, analysis_segment.end_time))
        for event in events:
            anchors.append((max(0.0, event.timestamp - 1.0), min(duration, event.timestamp + 1.0)))
        if not anchors and self.settings.opportunity_fallback_behavior == "timeline":
            anchors.append((0.0, min(duration, float(self.settings.opportunity_min_duration_seconds))))
        windows = [
            CandidateOpportunity(*_bounded_window(start, end, duration, self.settings), 0.0)
            for start, end in anchors
        ]
        return self._merge_windows(windows, duration)

    def _merge_windows(
        self, windows: list[CandidateOpportunity], source_duration: float
    ) -> list[CandidateOpportunity]:
        merged: list[CandidateOpportunity] = []
        for candidate in sorted(windows, key=lambda item: (item.start_time, item.end_time)):
            if not merged:
                merged.append(candidate)
                continue
            prior = merged[-1]
            overlap = _overlap(
                prior.start_time, prior.end_time, candidate.start_time, candidate.end_time
            )
            denominator = min(prior.end_time - prior.start_time, candidate.end_time - candidate.start_time)
            percentage = overlap / denominator if denominator else 0.0
            if percentage >= self.settings.opportunity_merge_overlap:
                start = min(prior.start_time, candidate.start_time)
                end = max(prior.end_time, candidate.end_time)
                start, end = _bounded_window(start, end, source_duration, self.settings)
                merged[-1] = CandidateOpportunity(start, end, max(prior.overlap_percentage, percentage))
            else:
                merged.append(candidate)
        return merged


def opportunity_provider_for(settings: Settings) -> OpportunityProvider:
    if settings.opportunity_provider == "rule":
        return RuleOpportunityProvider(settings)
    raise ProductionError("OPPORTUNITY_PROVIDER_INVALID", "configured opportunity provider is unavailable")


def _coverage(
    start: float, end: float, intervals: list[tuple[float, float]], duration: float
) -> float:
    if duration <= 0:
        return 0.0
    return min(1.0, sum(_overlap(start, end, left, right) for left, right in intervals) / duration)


def _score_reasons(
    candidate: CandidateOpportunity,
    analysis: VideoAnalysis,
    segments: list[AnalysisSegment],
    transcript: list[TranscriptSegment],
    events: list[AnalysisEvent],
    settings: Settings,
) -> list[ScoreReason]:
    duration = candidate.end_time - candidate.start_time
    by_type: dict[str, list[AnalysisSegment]] = {}
    for segment in segments:
        by_type.setdefault(segment.segment_type, []).append(segment)
    speech_intervals = [(item.start_time, item.end_time) for item in transcript]
    speech_intervals.extend((item.start_time, item.end_time) for item in by_type.get("SPEECH", []))
    motion_intervals = [
        (item.start_time, item.end_time)
        for kind in ("MOTION", "HIGH_MOTION")
        for item in by_type.get(kind, [])
    ]
    scene_intervals = [
        (item.start_time, item.end_time)
        for kind in ("SCENE", "SHOT_CHANGE")
        for item in by_type.get(kind, [])
    ]
    audio_intervals = [
        (item.start_time, item.end_time)
        for kind in ("LOUD_AUDIO", "AUDIO_PEAK")
        for item in by_type.get(kind, [])
    ]
    silence_intervals = [
        (item.start_time, item.end_time) for item in by_type.get("SILENCE", [])
    ]
    ocr_count = sum(
        1
        for event in events
        if event.event_type == "TEXT_DETECTED" and candidate.start_time <= event.timestamp <= candidate.end_time
    )
    event_count = sum(
        1 for event in events if candidate.start_time <= event.timestamp <= candidate.end_time
    )
    transcript_confidence = [item.confidence for item in transcript if item.confidence is not None]
    visual_score = min(
        1.0,
        ((analysis.width or 0) / 1920 + (analysis.height or 0) / 1080 + (analysis.fps or 0) / 30)
        / 3,
    )
    return [
        ScoreReason("SPEECH_QUALITY", _coverage(candidate.start_time, candidate.end_time, speech_intervals, duration), settings.opportunity_speech_weight, {"speech_intervals": len(speech_intervals)}),
        ScoreReason("MOTION", _coverage(candidate.start_time, candidate.end_time, motion_intervals, duration), settings.opportunity_motion_weight, {"motion_intervals": len(motion_intervals)}),
        ScoreReason("SCENE_CHANGE", _coverage(candidate.start_time, candidate.end_time, scene_intervals, duration), settings.opportunity_scene_weight, {"scene_intervals": len(scene_intervals)}),
        ScoreReason("TRANSCRIPT_CONFIDENCE", sum(transcript_confidence) / len(transcript_confidence) if transcript_confidence else 0.5 if transcript else 0.0, settings.opportunity_transcript_weight, {"transcript_segments": len(transcript)}),
        ScoreReason("OCR_ACTIVITY", min(1.0, ocr_count / 3), settings.opportunity_ocr_weight, {"event_count": ocr_count}),
        ScoreReason("AUDIO_ENERGY", _coverage(candidate.start_time, candidate.end_time, audio_intervals, duration), settings.opportunity_audio_weight, {"audio_intervals": len(audio_intervals)}),
        ScoreReason("SILENCE_CONTEXT", 1 - _coverage(candidate.start_time, candidate.end_time, silence_intervals, duration), settings.opportunity_silence_weight, {"silence_intervals": len(silence_intervals)}),
        ScoreReason("INTERESTING_EVENTS", min(1.0, event_count / 4), settings.opportunity_event_weight, {"event_count": event_count}),
        ScoreReason("VISUAL_ACTIVITY", visual_score, settings.opportunity_visual_weight, {"width": analysis.width, "height": analysis.height, "fps": analysis.fps}),
    ]


def _overall_score(reasons: list[ScoreReason]) -> float:
    total_weight = sum(reason.weight for reason in reasons)
    if total_weight <= 0:
        return 0.0
    return round(100 * sum(reason.score * reason.weight for reason in reasons) / total_weight, 2)


def _explanation(reasons: list[ScoreReason]) -> str:
    strongest = sorted(reasons, key=lambda reason: reason.score * reason.weight, reverse=True)[:3]
    if not strongest:
        return "No qualifying analysis signals were available."
    return "Ranked for " + ", ".join(
        f"{reason.reason_type.lower().replace('_', ' ')} ({reason.score:.0%})"
        for reason in strongest
    ) + "."


def request_opportunity_generation(
    session: Session, actor_id: uuid.UUID, analysis: VideoAnalysis, rerun: bool = False
) -> OpportunityGenerationRun:
    if not get_settings().opportunity_enabled:
        raise ProductionError("OPPORTUNITIES_DISABLED", "opportunity detection is disabled")
    if analysis.status != AnalysisStatus.COMPLETED:
        raise ProductionError("ANALYSIS_NOT_READY", "complete analysis is required before detecting opportunities")
    latest = session.scalar(
        select(OpportunityGenerationRun)
        .where(OpportunityGenerationRun.analysis_id == analysis.id)
        .order_by(OpportunityGenerationRun.generation_version.desc())
    )
    if latest is not None and latest.status == OpportunityRunStatus.RUNNING:
        if rerun:
            raise ProductionError("OPPORTUNITY_GENERATION_RUNNING", "opportunity generation is already running")
        return latest
    if latest is not None and latest.status == OpportunityRunStatus.COMPLETED and not rerun:
        return latest
    if latest is not None and rerun:
        run = OpportunityGenerationRun(
            analysis_id=analysis.id,
            project_id=analysis.project_id,
            brand_id=analysis.brand_id,
            generation_version=latest.generation_version + 1,
            status=OpportunityRunStatus.QUEUED,
            provider_name=get_settings().opportunity_provider,
        )
        session.add(run)
    elif latest is not None:
        latest.status = OpportunityRunStatus.QUEUED
        latest.started_at = None
        latest.completed_at = None
        latest.error_summary = None
        run = latest
    else:
        run = OpportunityGenerationRun(
            analysis_id=analysis.id,
            project_id=analysis.project_id,
            brand_id=analysis.brand_id,
            generation_version=1,
            status=OpportunityRunStatus.QUEUED,
            provider_name=get_settings().opportunity_provider,
        )
        session.add(run)
    session.flush()
    session.add(AuditEvent(actor_id=actor_id, entity_type="opportunity_generation_run", entity_id=run.id, brand_id=run.brand_id, event_name="opportunity.generation.queued", payload={"analysis_id": str(analysis.id), "version": run.generation_version}))
    session.commit()
    return run


def execute_opportunity_generation(
    session: Session,
    actor_id: uuid.UUID,
    run: OpportunityGenerationRun,
    provider: OpportunityProvider | None = None,
    settings: Settings | None = None,
) -> OpportunityGenerationRun:
    settings = settings or get_settings()
    if run.status in {OpportunityRunStatus.RUNNING, OpportunityRunStatus.COMPLETED}:
        return run
    analysis = session.get(VideoAnalysis, run.analysis_id)
    if analysis is None or analysis.status != AnalysisStatus.COMPLETED:
        raise ProductionError("ANALYSIS_NOT_READY", "complete analysis is required before detecting opportunities")
    run.status = OpportunityRunStatus.RUNNING
    run.started_at = datetime.now(UTC)
    session.add(AuditEvent(actor_id=actor_id, entity_type="opportunity_generation_run", entity_id=run.id, brand_id=run.brand_id, event_name="opportunity.generation.started"))
    session.commit()
    logger.info("opportunity_generation_started", run_id=str(run.id), analysis_id=str(analysis.id))
    try:
        segments = list(session.scalars(select(AnalysisSegment).where(AnalysisSegment.analysis_id == analysis.id)))
        transcript = list(session.scalars(select(TranscriptSegment).where(TranscriptSegment.analysis_id == analysis.id)))
        events = list(session.scalars(select(AnalysisEvent).where(AnalysisEvent.analysis_id == analysis.id)))
        candidates = (provider or opportunity_provider_for(settings)).detect(
            analysis, segments, transcript, events
        )
        if run.generation_version > 1:
            for stale in session.scalars(
                select(ClipOpportunity).where(
                    ClipOpportunity.analysis_id == analysis.id,
                    ClipOpportunity.generation_version < run.generation_version,
                    ClipOpportunity.review_status == OpportunityReviewStatus.PENDING,
                )
            ):
                stale.review_status = OpportunityReviewStatus.STALE
                stale.review_version += 1
                session.add(
                    ClipOpportunityVersion(
                        opportunity_id=stale.id,
                        version=stale.review_version,
                        review_status=stale.review_status,
                        generation_status=stale.generation_status,
                        actor_id=actor_id,
                        decision_reason="Superseded by explicit opportunity regeneration.",
                    )
                )
        persisted: list[ClipOpportunity] = []
        for candidate in candidates:
            reasons = _score_reasons(candidate, analysis, segments, transcript, events, settings)
            score = _overall_score(reasons)
            if score < settings.opportunity_min_score:
                continue
            confidence = round(
                sum(reason.score for reason in reasons) / len(reasons) if reasons else 0.0, 3
            )
            opportunity = ClipOpportunity(
                analysis_id=analysis.id,
                project_id=analysis.project_id,
                brand_id=analysis.brand_id,
                generation_version=run.generation_version,
                start_time=candidate.start_time,
                end_time=candidate.end_time,
                duration_seconds=round(candidate.end_time - candidate.start_time, 3),
                confidence=confidence,
                overall_score=score,
                overlap_percentage=round(candidate.overlap_percentage, 3),
                explanation=_explanation(reasons),
            )
            session.add(opportunity)
            session.flush()
            session.add_all(
                [
                    OpportunityReason(
                        opportunity_id=opportunity.id,
                        reason_type=reason.reason_type,
                        score=round(reason.score, 4),
                        weight=reason.weight,
                        metadata_json=reason.metadata,
                    )
                    for reason in reasons
                ]
            )
            session.add(
                ClipOpportunityVersion(
                    opportunity_id=opportunity.id,
                    version=opportunity.review_version,
                    review_status=opportunity.review_status,
                    generation_status=opportunity.generation_status,
                    actor_id=actor_id,
                    decision_reason="Generated from stored analysis.",
                )
            )
            persisted.append(opportunity)
        ranked = sorted(persisted, key=lambda item: (-item.overall_score, item.start_time))[
            : settings.opportunity_max_count
        ]
        for excess in (item for item in persisted if item.id not in {ranked_item.id for ranked_item in ranked}):
            excess.review_status = OpportunityReviewStatus.STALE
            excess.explanation += " Excluded by the configured opportunity count."
        run.opportunity_count = len(ranked)
        run.status = OpportunityRunStatus.COMPLETED
        run.completed_at = datetime.now(UTC)
        session.add(AuditEvent(actor_id=actor_id, entity_type="opportunity_generation_run", entity_id=run.id, event_name="opportunity.generation.completed", payload={"count": run.opportunity_count}))
        session.commit()
        logger.info("opportunity_generation_completed", run_id=str(run.id), count=run.opportunity_count)
        return run
    except Exception as error:
        session.rollback()
        persisted_run = session.get(OpportunityGenerationRun, run.id)
        assert persisted_run is not None
        persisted_run.status = OpportunityRunStatus.FAILED
        persisted_run.completed_at = datetime.now(UTC)
        persisted_run.error_summary = type(error).__name__
        session.add(AuditEvent(actor_id=actor_id, entity_type="opportunity_generation_run", entity_id=persisted_run.id, event_name="opportunity.generation.failed", payload={"error": type(error).__name__}))
        session.commit()
        logger.warning("opportunity_generation_failed", run_id=str(run.id), error=type(error).__name__)
        raise


def decide_opportunity(
    session: Session,
    actor_id: uuid.UUID,
    opportunity: ClipOpportunity,
    approved: bool,
    expected_version: int,
    reason: str | None = None,
) -> ClipOpportunity:
    if opportunity.review_status == OpportunityReviewStatus.APPROVED and approved:
        return opportunity
    if opportunity.review_status == OpportunityReviewStatus.REJECTED and not approved:
        return opportunity
    if expected_version != opportunity.review_version:
        raise ProductionError("STALE_OPPORTUNITY_ACTION", "opportunity review changed; reopen it")
    if opportunity.review_status == OpportunityReviewStatus.STALE:
        raise ProductionError("STALE_OPPORTUNITY", "opportunity was superseded by a regenerated ranking")
    opportunity.review_status = OpportunityReviewStatus.APPROVED if approved else OpportunityReviewStatus.REJECTED
    opportunity.review_version += 1
    session.add(
        ClipOpportunityVersion(
            opportunity_id=opportunity.id,
            version=opportunity.review_version,
            review_status=opportunity.review_status,
            generation_status=opportunity.generation_status,
            actor_id=actor_id,
            decision_reason=reason,
        )
    )
    session.add(AuditEvent(actor_id=actor_id, entity_type="clip_opportunity", entity_id=opportunity.id, event_name="opportunity.approved" if approved else "opportunity.rejected"))
    session.commit()
    logger.info("opportunity_decided", opportunity_id=str(opportunity.id), approved=approved)
    return opportunity


def generate_approved_opportunity(
    session: Session,
    actor_id: uuid.UUID,
    opportunity: ClipOpportunity,
    storage: LocalFilesystemStorage,
) -> ProductionClip | None:
    if opportunity.review_status != OpportunityReviewStatus.APPROVED:
        raise ProductionError("OPPORTUNITY_NOT_APPROVED", "approve an opportunity before generating its clip")
    if opportunity.generated_clip_id is not None:
        return session.get(ProductionClip, opportunity.generated_clip_id)
    project = session.get(ProductionProject, opportunity.project_id)
    if project is None:
        raise ProductionError("PROJECT_NOT_FOUND", "opportunity project no longer exists")
    clip = render_clip_window(
        session, actor_id, project, storage, opportunity.start_time, opportunity.end_time
    )
    opportunity.generated_clip_id = clip.id
    opportunity.generation_status = (
        OpportunityGenerationStatus.SUCCEEDED
        if clip.render_status == "SUCCEEDED"
        else OpportunityGenerationStatus.FAILED
    )
    opportunity.generation_error = None if clip.render_status == "SUCCEEDED" else "render_failed"
    opportunity.review_version += 1
    session.add(
        ClipOpportunityVersion(
            opportunity_id=opportunity.id,
            version=opportunity.review_version,
            review_status=opportunity.review_status,
            generation_status=opportunity.generation_status,
            actor_id=actor_id,
            decision_reason="Generated one clip through the existing renderer.",
        )
    )
    session.add(AuditEvent(actor_id=actor_id, entity_type="clip_opportunity", entity_id=opportunity.id, event_name="opportunity.clip.generated", payload={"clip_id": str(clip.id), "render_status": clip.render_status}))
    session.commit()
    logger.info("opportunity_clip_generated", opportunity_id=str(opportunity.id), clip_id=str(clip.id), status=clip.render_status)
    return clip
