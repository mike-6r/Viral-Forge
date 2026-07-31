import uuid

from sqlalchemy import select

from app.analysis.models import (
    AnalysisEvent,
    AnalysisSegment,
    AnalysisStatus,
    TranscriptSegment,
    VideoAnalysis,
)
from app.common.config import Settings
from app.ingestion.storage import LocalFilesystemStorage
from app.opportunities.models import (
    ClipOpportunity,
    OpportunityGenerationStatus,
    OpportunityReason,
    OpportunityReviewStatus,
    OpportunityRunStatus,
)
from app.opportunities.service import (
    RuleOpportunityProvider,
    decide_opportunity,
    execute_opportunity_generation,
    generate_approved_opportunity,
    request_opportunity_generation,
)
from app.production.models import ProductionClip, ProductionProject
from tests.conftest import DEV_ACTOR_ID


def _analysis_fixture(session):  # type: ignore[no-untyped-def]
    project = ProductionProject(
        source_url=f"https://youtu.be/{uuid.uuid4().hex[:11]}",
        source_duration_seconds=60.0,
        source_storage_key="assets/mock-source.mp4",
        status="SOURCE_READY",
        created_actor_id=DEV_ACTOR_ID,
    )
    session.add(project)
    session.flush()
    analysis = VideoAnalysis(
        project_id=project.id,
        status=AnalysisStatus.COMPLETED,
        duration_seconds=60.0,
        width=1920,
        height=1080,
        fps=30.0,
    )
    session.add(analysis)
    session.flush()
    session.add_all(
        [
            AnalysisSegment(
                analysis_id=analysis.id,
                start_time=10.0,
                end_time=24.0,
                segment_type="SPEECH",
                confidence=0.9,
                metadata_json={},
            ),
            AnalysisSegment(
                analysis_id=analysis.id,
                start_time=18.0,
                end_time=19.0,
                segment_type="SCENE",
                confidence=0.8,
                metadata_json={},
            ),
            AnalysisSegment(
                analysis_id=analysis.id,
                start_time=20.0,
                end_time=22.0,
                segment_type="HIGH_MOTION",
                confidence=0.8,
                metadata_json={},
            ),
            AnalysisSegment(
                analysis_id=analysis.id,
                start_time=21.0,
                end_time=23.0,
                segment_type="LOUD_AUDIO",
                confidence=0.7,
                metadata_json={},
            ),
            TranscriptSegment(
                analysis_id=analysis.id,
                start_time=10.0,
                end_time=24.0,
                text="A verified timeline signal for the opportunity engine.",
                confidence=0.95,
            ),
            AnalysisEvent(
                analysis_id=analysis.id,
                timestamp=20.0,
                event_type="SHOT_CHANGE",
                confidence=0.9,
                metadata_json={},
            ),
            AnalysisEvent(
                analysis_id=analysis.id,
                timestamp=21.0,
                event_type="TEXT_DETECTED",
                confidence=0.9,
                metadata_json={"text": "TEST"},
            ),
        ]
    )
    session.commit()
    return project, analysis


def test_opportunity_detection_scores_explains_and_merges(session):  # type: ignore[no-untyped-def]
    _, analysis = _analysis_fixture(session)
    settings = Settings(opportunity_min_score=0, opportunity_max_count=5)
    run = request_opportunity_generation(session, DEV_ACTOR_ID, analysis)
    completed = execute_opportunity_generation(session, DEV_ACTOR_ID, run, settings=settings)
    opportunities = list(
        session.scalars(
            select(ClipOpportunity).where(ClipOpportunity.generation_version == completed.generation_version)
        )
    )
    assert completed.status == OpportunityRunStatus.COMPLETED
    assert len(opportunities) == 1
    opportunity = opportunities[0]
    reasons = list(
        session.scalars(select(OpportunityReason).where(OpportunityReason.opportunity_id == opportunity.id))
    )
    assert opportunity.overall_score > 0 and "Ranked for" in opportunity.explanation
    assert {reason.reason_type for reason in reasons} >= {
        "SPEECH_QUALITY",
        "MOTION",
        "SCENE_CHANGE",
        "AUDIO_ENERGY",
        "SILENCE_CONTEXT",
        "OCR_ACTIVITY",
    }
    assert request_opportunity_generation(session, DEV_ACTOR_ID, analysis).id == completed.id


def test_rule_provider_merges_nearly_identical_windows(session):
    analysis = VideoAnalysis(status=AnalysisStatus.COMPLETED, duration_seconds=60.0)
    segments = [
        AnalysisSegment(start_time=10.0, end_time=15.0, segment_type="SPEECH", metadata_json={}),
        AnalysisSegment(start_time=12.0, end_time=16.0, segment_type="SCENE", metadata_json={}),
    ]
    windows = RuleOpportunityProvider(Settings(opportunity_min_score=0)).detect(
        analysis, segments, [], []
    )
    assert len(windows) == 1 and windows[0].overlap_percentage > 0


def test_approved_opportunity_generates_exactly_one_clip(session, tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    project, analysis = _analysis_fixture(session)
    run = request_opportunity_generation(session, DEV_ACTOR_ID, analysis)
    execute_opportunity_generation(
        session, DEV_ACTOR_ID, run, settings=Settings(opportunity_min_score=0)
    )
    opportunity = session.scalar(select(ClipOpportunity).where(ClipOpportunity.project_id == project.id))
    assert opportunity is not None

    def fake_render(*_: object, **__: object) -> ProductionClip:
        clip = ProductionClip(
            project_id=project.id,
            clip_number=1,
            start_seconds=opportunity.start_time,
            end_seconds=opportunity.end_time,
            duration_seconds=opportunity.duration_seconds,
            render_status="SUCCEEDED",
        )
        session.add(clip)
        session.commit()
        return clip

    monkeypatch.setattr("app.opportunities.service.render_clip_window", fake_render)
    approved = decide_opportunity(
        session, DEV_ACTOR_ID, opportunity, True, opportunity.review_version
    )
    storage = LocalFilesystemStorage(tmp_path / "storage")
    first = generate_approved_opportunity(session, DEV_ACTOR_ID, approved, storage)
    repeated = decide_opportunity(session, DEV_ACTOR_ID, approved, True, expected_version=1)
    second = generate_approved_opportunity(session, DEV_ACTOR_ID, repeated, storage)
    assert first is not None and second is not None and first.id == second.id
    assert approved.review_status == OpportunityReviewStatus.APPROVED
    assert approved.generation_status == OpportunityGenerationStatus.SUCCEEDED
    assert len(list(session.scalars(select(ProductionClip).where(ProductionClip.project_id == project.id)))) == 1


def test_opportunity_api_queues_and_lists(client, session):  # type: ignore[no-untyped-def]
    project, analysis = _analysis_fixture(session)
    headers = {"X-Development-Actor": str(DEV_ACTOR_ID)}
    queued = client.post(f"/api/v1/production/projects/{project.id}/opportunities", headers=headers)
    assert queued.status_code == 202, queued.text
    run = request_opportunity_generation(session, DEV_ACTOR_ID, analysis)
    execute_opportunity_generation(session, DEV_ACTOR_ID, run, settings=Settings(opportunity_min_score=0))
    opportunities = client.get(
        f"/api/v1/production/projects/{project.id}/opportunities", headers=headers
    )
    assert opportunities.status_code == 200 and opportunities.json()
    opportunity_id = opportunities.json()[0]["id"]
    assert client.get(f"/api/v1/opportunities/{opportunity_id}/reasons", headers=headers).json()
