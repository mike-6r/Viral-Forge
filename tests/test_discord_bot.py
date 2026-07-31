import uuid

import pytest
from sqlalchemy.orm import Session

from app.analysis.models import AnalysisEvent, AnalysisSegment, TranscriptSegment, VideoAnalysis
from app.common.config import Settings, get_settings
from app.content_packages.models import ContentPackage
from app.discord_bot import (
    ClipReviewView,
    ContentPackageReviewView,
    ControlCenterView,
    OpportunityReviewState,
    ProductionRepository,
    configured_role_ids,
    content_package_embed,
    control_center_embed,
    dashboard_embed,
    operational_status,
    opportunity_embed,
    run_bot,
)
from app.opportunities.models import ClipOpportunity, OpportunityReason
from app.production.models import ProductionClip, ProductionProject
from app.production.service import ProductionError


def test_discord_role_settings_and_safe_operational_status():
    settings = Settings(discord_allowed_role_ids="10, 20,invalid")
    assert configured_role_ids(settings) == frozenset()
    status = operational_status(Settings(discord_bot_token=None))
    assert set(status) == {
        "yt_dlp",
        "ffmpeg",
        "ffprobe",
        "discord_configured",
        "youtube_search_configured",
    }
    assert status["discord_configured"] is False


def test_bot_startup_requires_token(monkeypatch):
    monkeypatch.setenv("VIRALFORGE_DISCORD_BOT_TOKEN", "")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="DISCORD_BOT_TOKEN"):
        run_bot()
    get_settings.cache_clear()


def test_persistent_clip_navigation_boundaries_and_missing(session: Session, monkeypatch):
    project_id = uuid.uuid4()
    clips = [
        ProductionClip(
            project_id=project_id,
            clip_number=number,
            start_seconds=(number - 1) * 45,
            end_seconds=number * 45,
            duration_seconds=45,
            render_status="SUCCEEDED",
            discord_message_id=str(number),
        )
        for number in range(1, 3)
    ]
    session.add_all(clips)
    session.commit()

    def session_provider():  # type: ignore[no-untyped-def]
        yield session

    monkeypatch.setattr("app.discord_bot.get_session", session_provider)
    repository = ProductionRepository(Settings())
    first = repository.review_state(clips[0].id)
    second = repository.review_state(first.clip.id, 1)
    assert second.clip.id == clips[1].id
    assert repository.review_state(second.clip.id, -1).clip.id == clips[0].id
    first_view = ClipReviewView(first, repository, Settings(discord_allowed_role_ids="1"))
    last_view = ClipReviewView(second, repository, Settings(discord_allowed_role_ids="1"))
    assert first_view.previous.disabled and not first_view.next.disabled
    assert last_view.next.disabled and not last_view.previous.disabled
    assert repository.active_review_clips() == [clips[0].id, clips[1].id]
    with pytest.raises(ProductionError, match="no clip exists"):
        repository.review_state(first.clip.id, -1)
    with pytest.raises(ProductionError, match="no longer exists"):
        repository.review_state(uuid.uuid4())


def test_dashboard_compactly_displays_persisted_analysis(session: Session, monkeypatch):
    project = ProductionProject(
        source_url="https://youtu.be/AnalysisDashboard",
        source_duration_seconds=20.0,
        created_actor_id=uuid.UUID("a1111111-1111-1111-1111-111111111111"),
    )
    session.add(project)
    session.flush()
    analysis = VideoAnalysis(
        project_id=project.id,
        status="COMPLETED",
        duration_seconds=20.0,
        fps=30.0,
        width=1920,
        height=1080,
        transcript_language="en",
    )
    session.add(analysis)
    session.flush()
    session.add_all(
        [
            AnalysisSegment(
                analysis_id=analysis.id,
                start_time=0.0,
                end_time=3.0,
                segment_type="SCENE",
                metadata_json={},
            ),
            AnalysisSegment(
                analysis_id=analysis.id,
                start_time=3.0,
                end_time=5.0,
                segment_type="SPEECH",
                metadata_json={},
            ),
            TranscriptSegment(
                analysis_id=analysis.id,
                start_time=3.0,
                end_time=5.0,
                text="Test speech",
            ),
            AnalysisEvent(
                analysis_id=analysis.id,
                timestamp=3.0,
                event_type="SHOT_CHANGE",
                metadata_json={},
            ),
        ]
    )
    session.commit()

    def session_provider():  # type: ignore[no-untyped-def]
        yield session

    monkeypatch.setattr("app.discord_bot.get_session", session_provider)
    embed = dashboard_embed(ProductionRepository(Settings()).dashboard(project.id))
    values = " ".join(field.value for field in embed.fields)
    assert "COMPLETED" in values and "1 scenes" in values and "2.0s speech" in values


def test_control_center_summarizes_persisted_workflow(session: Session, monkeypatch):
    project = ProductionProject(
        source_url="https://youtu.be/ControlCenter",
        status="SOURCE_REVIEW_REQUIRED",
        created_actor_id=uuid.UUID("a1111111-1111-1111-1111-111111111111"),
    )
    session.add(project)
    session.commit()

    def session_provider():  # type: ignore[no-untyped-def]
        yield session

    monkeypatch.setattr("app.discord_bot.get_session", session_provider)
    repository = ProductionRepository(Settings())
    state = repository.control_center()
    embed = control_center_embed(state)
    assert state.total_projects == 1 and state.source_review_count == 1
    assert "1 source review" in " ".join(field.value for field in embed.fields)
    assert len(ControlCenterView(repository, Settings()).children) == 9


def test_opportunity_embed_displays_ranked_reason_and_transcript():
    opportunity = ClipOpportunity(
        analysis_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        start_time=10.0,
        end_time=35.0,
        duration_seconds=25.0,
        confidence=0.8,
        overall_score=72.5,
        overlap_percentage=0.2,
        explanation="Ranked for speech quality and scene change.",
    )
    reason = OpportunityReason(
        opportunity_id=opportunity.id,
        reason_type="SPEECH_QUALITY",
        score=0.9,
        weight=0.15,
        metadata_json={},
    )
    embed = opportunity_embed(OpportunityReviewState(opportunity, [reason], "Test transcript", 0, 1))
    values = " ".join(field.value for field in embed.fields)
    assert "72.5/100" in (embed.description or "") and "SPEECH_QUALITY" in values


def test_content_package_review_view_exposes_platform_selection_and_evidence():
    package = ContentPackage(
        clip_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        generation_version=1,
        status="PENDING",
        review_version=1,
        provider_name="local_template",
        model_name="deterministic-template",
        provider_version="v1",
        language="en",
        content_category="SOURCE_CLIP",
        confidence=0.7,
        explanation="Local evidence-bound package.",
        fields_json={"primary_hook": "Source hook", "youtube_shorts_title": "Source title"},
        verified_facts_json=["Source URL: persisted"],
        transcript_statements_json=["Persisted transcript statement"],
        uncertainty_json=["Review full context."],
        warnings_json=[],
    )
    repository = ProductionRepository(Settings())
    view = ContentPackageReviewView(package, repository, Settings(discord_allowed_role_ids="1"))
    embed = content_package_embed(package)
    assert len(view.children) == 4
    assert "Persisted transcript statement" in " ".join(field.value for field in embed.fields)
