import uuid

import pytest

from app.analysis.models import AnalysisEvent, AnalysisStatus, TranscriptSegment, VideoAnalysis
from app.content_packages.models import ContentPackage
from app.opportunities.models import ClipOpportunity, OpportunityReason
from app.producer.models import ClipQualityReport, ProducerRecommendationStatus
from app.producer.service import (
    decide_recommendation,
    generate_clip_quality_report,
    generate_clip_recommendations,
    generate_project_recommendations,
)
from app.production.models import ProductionClip, ProductionProject, ProductionSource
from app.production.service import ProductionError
from tests.conftest import DEV_ACTOR_ID


def _project_with_evidence(session):  # type: ignore[no-untyped-def]
    project = ProductionProject(
        source_url=f"https://www.youtube.com/watch?v={uuid.uuid4().hex[:11]}",
        source_title="Persisted evidence title",
        source_channel="Persisted channel",
        source_duration_seconds=75.0,
        status="SOURCE_READY",
        created_actor_id=DEV_ACTOR_ID,
    )
    session.add(project)
    session.flush()
    source = ProductionSource(
        project_id=project.id,
        source_url=project.source_url,
        uploader_name="Persisted channel",
        quality_score=82.0,
        quality_status="ACCEPTABLE",
        ownership_classification="OFFICIAL",
        watermark_status="NOT_DETECTED",
    )
    session.add(source)
    session.flush()
    project.selected_source_id = source.id
    analysis = VideoAnalysis(
        project_id=project.id,
        source_id=source.id,
        status=AnalysisStatus.COMPLETED,
        duration_seconds=75.0,
        progress_percent=100.0,
        transcript_language="en",
    )
    clip = ProductionClip(
        project_id=project.id,
        clip_number=1,
        start_seconds=8.0,
        end_seconds=38.0,
        duration_seconds=30.0,
        storage_key="clips/producer-test.mp4",
        render_status="SUCCEEDED",
    )
    session.add_all([analysis, clip])
    session.flush()
    session.add_all([
        TranscriptSegment(analysis_id=analysis.id, start_time=9.0, end_time=16.0, text="Persisted transcript evidence for a strong opening.", confidence=0.9, metadata_json={}),
        AnalysisEvent(analysis_id=analysis.id, timestamp=12.0, event_type="MOTION", confidence=0.8, metadata_json={}),
        ClipOpportunity(analysis_id=analysis.id, project_id=project.id, generation_version=1, start_time=8.0, end_time=38.0, duration_seconds=30.0, confidence=0.82, overall_score=82.0, explanation="Persisted opportunity.", generated_clip_id=clip.id),
        ContentPackage(clip_id=clip.id, project_id=project.id, generation_version=1, provider_name="local", language="en", content_category="SOURCE_CLIP", confidence=0.7, explanation="Persisted package", fields_json={"youtube_shorts_title": "Persisted title", "tiktok_caption": "Persisted caption", "hashtags": ["#persisted"]}),
    ])
    session.flush()
    opportunity = session.query(ClipOpportunity).filter_by(project_id=project.id).one()
    session.add(OpportunityReason(opportunity_id=opportunity.id, reason_type="SPEECH_QUALITY", score=0.9, weight=0.2, metadata_json={}))
    session.commit()
    return project, clip


def test_producer_recommendations_are_evidence_bound_and_advisory(session):  # type: ignore[no-untyped-def]
    project, _ = _project_with_evidence(session)
    original_status = project.status
    recommendations = generate_project_recommendations(session, DEV_ACTOR_ID, project)
    assert {item.recommendation_type for item in recommendations} >= {"SOURCE_TRUST", "DOWNLOAD", "PROCESS", "CLIP_STRATEGY", "CLIP_BOUNDARY"}
    assert all(item.evidence_json and item.reasoning and 0 <= item.confidence <= 1 for item in recommendations)
    assert session.get(ProductionProject, project.id).status == original_status
    recommendation = recommendations[0]
    decided = decide_recommendation(session, DEV_ACTOR_ID, recommendation, recommendation.review_version, True, {"note": "operator edit"})
    assert decided.status == ProducerRecommendationStatus.APPROVED
    assert decided.operator_edit_json == {"note": "operator edit"}
    assert session.get(ProductionProject, project.id).status == original_status


def test_producer_recommendation_uses_optimistic_locking(session):  # type: ignore[no-untyped-def]
    project, _ = _project_with_evidence(session)
    recommendation = generate_project_recommendations(session, DEV_ACTOR_ID, project)[0]
    with pytest.raises(ProductionError, match="reload"):
        decide_recommendation(session, DEV_ACTOR_ID, recommendation, recommendation.review_version + 1, False)


def test_quality_report_is_persisted_and_does_not_change_clip_review(session):  # type: ignore[no-untyped-def]
    _, clip = _project_with_evidence(session)
    report = generate_clip_quality_report(session, DEV_ACTOR_ID, clip)
    recommendations = generate_clip_recommendations(session, DEV_ACTOR_ID, clip)
    assert isinstance(report, ClipQualityReport)
    assert report.evidence_json
    assert 0 <= report.overall_readiness <= 100
    assert {item.recommendation_type for item in recommendations} == {
        "METADATA_VARIANT",
        "PUBLISH_READINESS",
    }
    assert session.get(ProductionClip, clip.id).approval_status == "PENDING"
