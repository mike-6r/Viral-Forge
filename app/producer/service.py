"""Evidence-bound, local AI Producer recommendations with no pipeline side effects."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.models import AnalysisEvent, AnalysisStatus, TranscriptSegment, VideoAnalysis
from app.analytics.models import PostAnalyticsSnapshot
from app.audit.models import AuditEvent
from app.content_packages.models import ContentPackage
from app.opportunities.models import ClipOpportunity, OpportunityReason
from app.producer.models import (
    ClipQualityReport,
    ProducerOutcomeEvaluation,
    ProducerRecommendation,
    ProducerRecommendationStatus,
    ProducerRecommendationType,
)
from app.production.models import ProductionClip, ProductionProject, ProductionSource
from app.production.service import ProductionError
from app.publishing.models import PublishReviewGate


def _bounded(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 1)


def _evidence(kind: str, value: object, note: str) -> dict[str, object]:
    return {"kind": kind, "value": value, "note": note}


def _analysis_for(session: Session, project_id: uuid.UUID) -> VideoAnalysis | None:
    return session.scalar(
        select(VideoAnalysis)
        .where(VideoAnalysis.project_id == project_id, VideoAnalysis.status == AnalysisStatus.COMPLETED)
        .order_by(VideoAnalysis.created_at.desc())
    )


def _source_for(session: Session, project: ProductionProject) -> ProductionSource | None:
    return session.get(ProductionSource, project.selected_source_id) if project.selected_source_id else None


def _new_recommendation(
    session: Session,
    *,
    brand_id: uuid.UUID,
    project_id: uuid.UUID | None,
    clip_id: uuid.UUID | None,
    content_package_id: uuid.UUID | None,
    recommendation_type: str,
    confidence: float,
    reasoning: str,
    evidence: list[dict[str, object]],
    recommendation: dict[str, object],
    prediction: dict[str, object],
) -> ProducerRecommendation:
    item = ProducerRecommendation(
        brand_id=brand_id,
        project_id=project_id,
        clip_id=clip_id,
        content_package_id=content_package_id,
        recommendation_type=recommendation_type,
        confidence=_bounded(confidence) / 100,
        reasoning=reasoning,
        evidence_json=evidence,
        recommendation_json=recommendation,
        prediction_json=prediction,
        provider_name="local_producer",
        model_name="deterministic-evidence-rules",
        provider_version="v1",
    )
    session.add(item)
    return item


def generate_project_recommendations(session: Session, actor_id: uuid.UUID | None, project: ProductionProject) -> list[ProducerRecommendation]:
    """Produce advisory decisions from existing persisted records only.

    Generation adds independent review records; it never calls download,
    analysis, rendering, queueing, or publishing services.
    """
    existing = list(
        session.scalars(
            select(ProducerRecommendation)
            .where(
                ProducerRecommendation.project_id == project.id,
                ProducerRecommendation.clip_id.is_(None),
                ProducerRecommendation.status == ProducerRecommendationStatus.PENDING,
            )
            .order_by(ProducerRecommendation.created_at)
        )
    )
    if existing:
        return existing
    source = _source_for(session, project)
    source_candidates = list(
        session.scalars(
            select(ProductionSource)
            .where(ProductionSource.project_id == project.id)
            .order_by(ProductionSource.quality_score.desc())
        )
    )
    analysis = _analysis_for(session, project.id)
    opportunities = list(session.scalars(select(ClipOpportunity).where(ClipOpportunity.project_id == project.id).order_by(ClipOpportunity.overall_score.desc())))
    evidence: list[dict[str, object]] = [_evidence("project_status", project.status, "Persisted project lifecycle state.")]
    trust = 40.0
    if source is not None:
        trust = source.quality_score or 0.0
        evidence.extend([
            _evidence("source_quality_score", source.quality_score, "Persisted source-quality score."),
            _evidence("ownership_classification", source.ownership_classification, "Persisted source ownership classification."),
            _evidence("watermark_status", source.watermark_status, "Persisted watermark inspection result."),
        ])
    best_candidate = source_candidates[0] if source_candidates else source
    better_source_exists = bool(
        source is not None
        and best_candidate is not None
        and best_candidate.id != source.id
        and best_candidate.quality_score > source.quality_score
    )
    evidence.append(_evidence("source_candidate_count", len(source_candidates), "Persisted alternative source candidates."))
    ready_for_download = source is not None and source.quality_status == "ACCEPTABLE"
    results = [
        _new_recommendation(
            session, brand_id=project.brand_id, project_id=project.id, clip_id=None, content_package_id=None,
            recommendation_type=ProducerRecommendationType.SOURCE_TRUST, confidence=trust,
            reasoning="Source trust is based on persisted source-quality, ownership, and watermark inspection fields; it is not a rights determination.",
            evidence=evidence, recommendation={"recommendation": "COMPARE_ALTERNATIVE_SOURCE" if better_source_exists else "REVIEW_SOURCE", "trusted_for_processing": ready_for_download, "better_source_candidate_id": str(best_candidate.id) if better_source_exists and best_candidate is not None else None, "operator_action_required": True},
            prediction={"metric": "source_quality_score", "predicted_value": trust},
        ),
        _new_recommendation(
            session, brand_id=project.brand_id, project_id=project.id, clip_id=None, content_package_id=None,
            recommendation_type=ProducerRecommendationType.DOWNLOAD, confidence=70.0 if ready_for_download else 25.0,
            reasoning="The producer recommends a download only when the selected persisted source is marked acceptable. Human source acceptance and rights decisions remain required.",
            evidence=evidence, recommendation={"recommendation": "DOWNLOAD" if ready_for_download else "DO_NOT_DOWNLOAD_YET", "operator_action_required": True},
            prediction={"metric": "processing_readiness", "predicted_value": 1 if ready_for_download else 0},
        ),
    ]
    if analysis is not None:
        analysis_evidence = evidence + [
            _evidence("analysis_progress", analysis.progress_percent, "Persisted analysis progress."),
            _evidence("analysis_duration_seconds", analysis.duration_seconds, "Persisted source duration."),
            _evidence("opportunity_count", len(opportunities), "Persisted candidate opportunity count."),
        ]
        best = opportunities[0] if opportunities else None
        suggested_count = min(3, len(opportunities))
        results.append(_new_recommendation(
            session, brand_id=project.brand_id, project_id=project.id, clip_id=None, content_package_id=None,
            recommendation_type=ProducerRecommendationType.PROCESS, confidence=80.0 if best else 35.0,
            reasoning="Processing readiness is based on completed persisted analysis and the presence of ranked, explainable opportunities.",
            evidence=analysis_evidence, recommendation={"recommendation": "REVIEW_CLIP_STRATEGY" if best else "REQUEST_MORE_CONTEXT", "operator_action_required": True},
            prediction={"metric": "opportunity_count", "predicted_value": suggested_count},
        ))
        if best is not None:
            reason_rows = list(session.scalars(select(OpportunityReason).where(OpportunityReason.opportunity_id == best.id)))
            reason_names = [row.reason_type for row in reason_rows]
            results.extend([
                _new_recommendation(
                    session, brand_id=project.brand_id, project_id=project.id, clip_id=None, content_package_id=None,
                    recommendation_type=ProducerRecommendationType.CLIP_STRATEGY, confidence=best.confidence * 100,
                    reasoning="Clip count and selection are recommendations derived from ranked opportunity windows, not an automatic rendering command.",
                    evidence=analysis_evidence + [_evidence("top_opportunity_score", best.overall_score, "Highest persisted opportunity score."), _evidence("opportunity_reasons", reason_names, "Persisted explainable ranking reasons.")],
                    recommendation={"recommended_clip_count": suggested_count, "top_opportunity_id": str(best.id), "operator_action_required": True},
                    prediction={"metric": "retention_estimate", "predicted_value": _bounded(best.overall_score)},
                ),
                _new_recommendation(
                    session, brand_id=project.brand_id, project_id=project.id, clip_id=None, content_package_id=None,
                    recommendation_type=ProducerRecommendationType.CLIP_BOUNDARY, confidence=best.confidence * 100,
                    reasoning="Suggested start and end are the persisted top-ranked opportunity window. Review source context before accepting it.",
                    evidence=analysis_evidence + [_evidence("opportunity_window", {"start": best.start_time, "end": best.end_time}, "Persisted candidate window.")],
                    recommendation={"opportunity_id": str(best.id), "suggested_start_seconds": best.start_time, "suggested_end_seconds": best.end_time, "suggested_duration_seconds": best.duration_seconds, "operator_action_required": True},
                    prediction={"metric": "retention_estimate", "predicted_value": _bounded(best.overall_score)},
                ),
            ])
    session.flush()
    for result in results:
        session.add(AuditEvent(actor_id=actor_id, entity_type="producer_recommendation", entity_id=result.id, brand_id=result.brand_id, event_name="producer.recommendation.generated", payload={"type": result.recommendation_type, "project_id": str(project.id)}))
    session.commit()
    return results


def generate_clip_quality_report(
    session: Session, actor_id: uuid.UUID | None, clip: ProductionClip, rerun: bool = False
) -> ClipQualityReport:
    if clip.render_status != "SUCCEEDED":
        raise ProductionError("CLIP_NOT_RENDERED", "a successfully rendered clip is required for a quality report")
    project = session.get(ProductionProject, clip.project_id)
    if project is None:
        raise ProductionError("PROJECT_NOT_FOUND", "clip project was not found")
    latest = session.scalar(
        select(ClipQualityReport)
        .where(ClipQualityReport.clip_id == clip.id)
        .order_by(ClipQualityReport.report_version.desc())
    )
    if latest is not None and not rerun:
        return latest
    analysis = _analysis_for(session, project.id)
    package = session.scalar(select(ContentPackage).where(ContentPackage.clip_id == clip.id).order_by(ContentPackage.generation_version.desc()))
    transcript_count = 0
    event_count = 0
    opportunity = session.scalar(select(ClipOpportunity).where(ClipOpportunity.generated_clip_id == clip.id))
    evidence: list[dict[str, object]] = [_evidence("clip_window", {"start": clip.start_seconds, "end": clip.end_seconds}, "Persisted rendered clip timing.")]
    if analysis is not None:
        transcript_count = len(list(session.scalars(select(TranscriptSegment.id).where(TranscriptSegment.analysis_id == analysis.id, TranscriptSegment.end_time >= clip.start_seconds, TranscriptSegment.start_time <= clip.end_seconds))))
        event_count = len(list(session.scalars(select(AnalysisEvent.id).where(AnalysisEvent.analysis_id == analysis.id, AnalysisEvent.timestamp >= clip.start_seconds, AnalysisEvent.timestamp <= clip.end_seconds))))
    evidence += [_evidence("transcript_segments", transcript_count, "Transcript coverage overlapping the clip."), _evidence("analysis_events", event_count, "Analysis events inside the clip window.")]
    opportunity_score = opportunity.overall_score if opportunity is not None else 45.0
    hook = _bounded(45 + min(30, transcript_count * 8) + min(20, event_count * 4))
    pacing = _bounded(55 + min(25, event_count * 5) + (10 if 15 <= clip.duration_seconds <= 75 else -10))
    context = _bounded(35 + min(35, transcript_count * 9) + (15 if clip.start_seconds > 0 else 0))
    subtitle = _bounded(30 + min(60, transcript_count * 12))
    metadata = package.fields_json if package is not None else {}
    title = 85.0 if metadata.get("youtube_shorts_title") else 25.0
    caption = 80.0 if metadata.get("tiktok_caption") or metadata.get("instagram_caption") else 25.0
    hashtags = 75.0 if metadata.get("hashtags") else 20.0
    retention = _bounded((hook * 0.45) + (pacing * 0.35) + (context * 0.20) + ((opportunity_score - 50) * 0.12))
    readiness = _bounded((hook + pacing + context + subtitle + title + caption + hashtags) / 7)
    report = ClipQualityReport(
        brand_id=clip.brand_id, project_id=clip.project_id, clip_id=clip.id,
        report_version=(latest.report_version + 1) if latest else 1,
        hook_quality=hook, pacing_quality=pacing, context_quality=context, retention_estimate=retention,
        subtitle_quality=subtitle, title_quality=title, caption_quality=caption, hashtag_quality=hashtags,
        overall_readiness=readiness,
        reasoning="Local producer quality scores are evidence-bound estimates from rendered timing, transcript coverage, analysis events, opportunity score, and persisted content-package fields. They are not predicted performance guarantees.",
        evidence_json=evidence,
        recommendations_json={"operator_action_required": True, "review_context": context < 60, "review_subtitles": subtitle < 60, "review_metadata": min(title, caption, hashtags) < 60},
        prediction_json={"retention_estimate": retention, "overall_readiness": readiness},
        provider_name="local_producer", model_name="deterministic-evidence-rules", provider_version="v1",
    )
    session.add(report)
    session.flush()
    session.add(AuditEvent(actor_id=actor_id, entity_type="clip_quality_report", entity_id=report.id, brand_id=report.brand_id, event_name="producer.clip_quality.generated", payload={"clip_id": str(clip.id), "report_version": report.report_version}))
    session.commit()
    return report


def generate_clip_recommendations(
    session: Session, actor_id: uuid.UUID | None, clip: ProductionClip, rerun: bool = False
) -> list[ProducerRecommendation]:
    """Recommend only metadata and readiness decisions for a rendered clip."""
    project = session.get(ProductionProject, clip.project_id)
    if project is None:
        raise ProductionError("PROJECT_NOT_FOUND", "clip project was not found")
    existing = list(
        session.scalars(
            select(ProducerRecommendation)
            .where(
                ProducerRecommendation.clip_id == clip.id,
                ProducerRecommendation.recommendation_type.in_([
                    ProducerRecommendationType.METADATA_VARIANT,
                    ProducerRecommendationType.PUBLISH_READINESS,
                ]),
            )
            .order_by(ProducerRecommendation.created_at)
        )
    )
    if existing and not rerun:
        return existing
    package = session.scalar(
        select(ContentPackage)
        .where(ContentPackage.clip_id == clip.id)
        .order_by(ContentPackage.generation_version.desc())
    )
    gate = session.scalar(select(PublishReviewGate).where(PublishReviewGate.clip_id == clip.id))
    fields = package.fields_json if package is not None else {}
    title = fields.get("youtube_shorts_title")
    captions = [key for key in ("tiktok_caption", "instagram_caption", "facebook_caption", "x_post") if fields.get(key)]
    metadata_evidence = [
        _evidence("render_status", clip.render_status, "Persisted rendered media status."),
        _evidence("source_title", project.source_title, "Persisted source title; no new factual title is invented."),
        _evidence("content_package_status", package.status if package is not None else None, "Persisted package review state."),
        _evidence("metadata_fields", sorted(fields), "Persisted editable metadata fields."),
    ]
    metadata_ready = bool(title and captions)
    result = [_new_recommendation(
        session, brand_id=clip.brand_id, project_id=clip.project_id, clip_id=clip.id,
        content_package_id=package.id if package is not None else None,
        recommendation_type=ProducerRecommendationType.METADATA_VARIANT,
        confidence=80.0 if metadata_ready else 30.0,
        reasoning="Metadata advice is limited to the existing evidence-bound content package. The operator chooses, edits, and approves the final variant.",
        evidence=metadata_evidence,
        recommendation={"recommended_title": title, "available_caption_variants": captions, "operator_action_required": True, "misleading_claim_check": "Review against the persisted source and transcript before use."},
        prediction={"metric": "metadata_readiness", "predicted_value": 1 if metadata_ready else 0},
    )]
    gates_ready = (
        clip.approval_status == "APPROVED"
        and package is not None
        and package.status == "APPROVED"
        and gate is not None
        and gate.moderation_disposition == "APPROVED"
        and (not gate.rights_required or gate.rights_disposition == "APPROVED")
    )
    result.append(_new_recommendation(
        session, brand_id=clip.brand_id, project_id=clip.project_id, clip_id=clip.id,
        content_package_id=package.id if package is not None else None,
        recommendation_type=ProducerRecommendationType.PUBLISH_READINESS,
        confidence=90.0 if gates_ready else 15.0,
        reasoning="Publish readiness reflects persisted clip, package, moderation, and rights gates only. It never creates a publish request or uploads media.",
        evidence=metadata_evidence + [
            _evidence("clip_approval", clip.approval_status, "Persisted creative review status."),
            _evidence("moderation_disposition", gate.moderation_disposition if gate is not None else None, "Persisted moderation gate."),
            _evidence("rights_disposition", gate.rights_disposition if gate is not None else None, "Persisted rights gate."),
        ],
        recommendation={"recommendation": "READY_FOR_EXPLICIT_PUBLISH_DECISION" if gates_ready else "NOT_READY_TO_PUBLISH", "operator_action_required": True},
        prediction={"metric": "publish_readiness", "predicted_value": 1 if gates_ready else 0},
    ))
    session.flush()
    for item in result:
        session.add(AuditEvent(actor_id=actor_id, entity_type="producer_recommendation", entity_id=item.id, brand_id=item.brand_id, event_name="producer.recommendation.generated", payload={"type": item.recommendation_type, "clip_id": str(clip.id)}))
    session.commit()
    return result


def decide_recommendation(session: Session, actor_id: uuid.UUID, item: ProducerRecommendation, expected_version: int, approved: bool, operator_edits: dict[str, object] | None = None, reason: str | None = None) -> ProducerRecommendation:
    target = ProducerRecommendationStatus.APPROVED if approved else ProducerRecommendationStatus.REJECTED
    if item.status == target:
        return item
    if item.review_version != expected_version:
        raise ProductionError("PRODUCER_RECOMMENDATION_VERSION_CONFLICT", "recommendation changed; reload before deciding")
    if item.status != ProducerRecommendationStatus.PENDING:
        raise ProductionError("PRODUCER_RECOMMENDATION_NOT_REVIEWABLE", "recommendation is not pending review")
    item.status = target
    item.operator_edit_json = operator_edits or item.operator_edit_json
    item.decided_by_id = actor_id
    item.decision_reason = reason
    item.review_version += 1
    session.add(AuditEvent(actor_id=actor_id, entity_type="producer_recommendation", entity_id=item.id, brand_id=item.brand_id, event_name="producer.recommendation.approved" if approved else "producer.recommendation.rejected", reason=reason, payload={"operator_edits": bool(operator_edits), "pipeline_changed": False}))
    session.commit()
    return item


def evaluate_predictions(session: Session, actor_id: uuid.UUID | None = None, brand_id: uuid.UUID | None = None) -> int:
    """Persist comparisons with official analytics snapshots; never tune settings."""
    query = select(ProducerRecommendation)
    if brand_id is not None:
        query = query.where(ProducerRecommendation.brand_id == brand_id)
    created = 0
    for recommendation in session.scalars(query):
        clip_id = recommendation.clip_id or session.scalar(
            select(ProductionClip.id)
            .where(ProductionClip.project_id == recommendation.project_id)
            .order_by(ProductionClip.created_at.desc())
        )
        if clip_id is None:
            continue
        snapshot = session.scalar(select(PostAnalyticsSnapshot).where(PostAnalyticsSnapshot.clip_id == clip_id).order_by(PostAnalyticsSnapshot.captured_at.desc()))
        if snapshot is None:
            continue
        existing = session.scalar(select(ProducerOutcomeEvaluation).where(ProducerOutcomeEvaluation.recommendation_id == recommendation.id, ProducerOutcomeEvaluation.snapshot_id == snapshot.id))
        if existing is not None:
            continue
        observed = {"views": snapshot.views, "average_view_duration_seconds": snapshot.average_view_duration_seconds, "retention_percentage": snapshot.retention_percentage}
        evaluation = ProducerOutcomeEvaluation(brand_id=recommendation.brand_id, recommendation_id=recommendation.id, clip_id=clip_id, snapshot_id=snapshot.id, predicted_json=recommendation.prediction_json, observed_json=observed, evaluation_json={"comparison_available": True, "note": "Stored for operator review; no settings were modified."})
        session.add(evaluation)
        created += 1
    if created:
        session.commit()
    return created
