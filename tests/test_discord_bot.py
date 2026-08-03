import uuid

import pytest
from sqlalchemy.orm import Session

from app.analysis.models import AnalysisEvent, AnalysisSegment, TranscriptSegment, VideoAnalysis
from app.brands.models import Brand, BrandMembership, Workspace
from app.common.config import Settings, get_settings
from app.content_packages.models import ContentPackage
from app.discord_bot import (
    ClipReviewView,
    ContentPackageReviewView,
    ContentReadySetupView,
    DiscoveryRepository,
    DiscoverySetupView,
    MediaQualityView,
    OperatorAccessHelpView,
    OperatorHomeView,
    OpportunityReviewState,
    ProducerRecommendationView,
    ProductionRepository,
    configured_role_ids,
    content_package_embed,
    control_center_embed,
    dashboard_embed,
    guided_project_embed,
    operational_status,
    opportunity_embed,
    producer_advice_embed,
    run_bot,
)
from app.opportunities.models import (
    ClipOpportunity,
    OpportunityGenerationRun,
    OpportunityReason,
    OpportunityReviewStatus,
)
from app.producer.models import ProducerRecommendation
from app.production.models import ProductionClip, ProductionProject
from app.production.service import ProductionError
from app.rendered_media.models import RenderedMediaInspection


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
    clips[0].brand_id = uuid.uuid4()
    session.commit()
    project_clip = repository.first_pending_clip_for_project(project_id)
    assert project_clip is not None and project_clip.clip.id == clips[0].id
    with pytest.raises(ProductionError, match="no clip exists"):
        repository.review_state(first.clip.id, -1)
    with pytest.raises(ProductionError, match="no longer exists"):
        repository.review_state(uuid.uuid4())


def test_manual_project_creation_uses_the_selected_brand(session: Session, monkeypatch):
    workspace = Workspace(name="Selected workspace", slug="selected-workspace")
    session.add(workspace)
    session.flush()
    brand = Brand(
        workspace_id=workspace.id,
        name="Selected brand",
        slug="selected-brand",
        description=None,
    )
    session.add(brand)
    session.flush()
    session.add(
        BrandMembership(
            brand_id=brand.id,
            user_id=uuid.UUID("a1111111-1111-1111-1111-111111111111"),
            role="ADMIN",
            is_default=True,
        )
    )
    session.commit()

    def session_provider():  # type: ignore[no-untyped-def]
        yield session

    captured: dict[str, uuid.UUID] = {}

    def create_for_brand(*_: object, brand_id: uuid.UUID, **__: object) -> ProductionProject:
        captured["brand_id"] = brand_id
        return ProductionProject(
            source_url="https://youtu.be/SelectedBrand",
            created_actor_id=uuid.UUID("a1111111-1111-1111-1111-111111111111"),
        )

    monkeypatch.setattr("app.discord_bot.get_session", session_provider)
    monkeypatch.setattr("app.discord_bot.create_project", create_for_brand)
    ProductionRepository(Settings()).create_project("https://youtu.be/SelectedBrand")
    assert captured["brand_id"] == brand.id


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


def test_guided_project_displays_persisted_download_progress(session: Session, monkeypatch):
    project = ProductionProject(
        source_url="https://youtu.be/DownloadProgress",
        status="DOWNLOADING",
        download_progress_percent=45,
        download_progress_stage="DOWNLOADING",
        created_actor_id=uuid.UUID("a1111111-1111-1111-1111-111111111111"),
    )
    session.add(project)
    session.commit()

    def session_provider():  # type: ignore[no-untyped-def]
        yield session

    monkeypatch.setattr("app.discord_bot.get_session", session_provider)
    embed = guided_project_embed(ProductionRepository(Settings()).dashboard(project.id))
    values = " ".join(field.value for field in embed.fields)
    assert "45% complete" in values
    assert "Download" in " ".join(field.name for field in embed.fields)
    assert project.source_url not in values
    assert len(embed.fields) <= 4
    assert embed.footer.text == (
        "If this card stops responding after a bot restart, use /viralforge home to reopen your active work."
    )


def test_media_quality_view_can_refresh_a_persisted_inspection(session: Session, monkeypatch):
    item = RenderedMediaInspection(
        brand_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        clip_id=uuid.uuid4(),
        inspection_version=1,
        status="RUNNING",
        current_stage="SAMPLE_FRAMES",
        progress_percent=45.0,
    )
    session.add(item)
    session.commit()

    def session_provider():  # type: ignore[no-untyped-def]
        yield session

    monkeypatch.setattr("app.discord_bot.get_session", session_provider)
    repository = ProductionRepository(Settings())
    current = repository.rendered_media_quality(item.id)
    view = MediaQualityView(current, repository, Settings(discord_allowed_role_ids="1"))
    assert current.current_stage == "SAMPLE_FRAMES"
    assert "Refresh Status" in [child.label for child in view.children]


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
    assert state.active_brand_name
    assert "1 source awaiting review" in " ".join(field.value for field in embed.fields)
    assert len(OperatorHomeView(repository, Settings()).children) == 5
    assert [child.label for child in OperatorHomeView(repository, Settings()).children] == [
        "View Progress",
        "Find Sources",
        "Add Video",
        "Switch Brand",
        "Refresh",
    ]


def test_discovery_setup_wizard_has_a_supported_path_and_safe_alternatives():
    view = DiscoverySetupView(
        repository=DiscoveryRepository(Settings()),
        settings=Settings(),
        brand_name="BodycamsDailyHQ",
    )
    select = next(child for child in view.children if hasattr(child, "options"))
    assert [option.label for option in select.options] == [
        "Manual Video",
        "YouTube Channel",
        "YouTube Playlist",
        "RSS Feed",
        "Website",
    ]
    assert [child.label for child in view.children if hasattr(child, "label")] == [
        "Continue",
        "Help",
        "Back to Workspace",
    ]


def test_guided_empty_state_views_keep_an_actionable_next_step():
    settings = Settings(discord_allowed_role_ids="123")
    access = OperatorAccessHelpView(settings)
    publishing = ContentReadySetupView(ProductionRepository(settings), settings)
    assert [child.label for child in access.children] == [
        "View Required Roles",
        "Contact Administrator",
        "Back",
    ]
    assert [child.label for child in publishing.children] == [
        "Set Up YouTube",
        "Set Up TikTok",
        "Find Content Now",
        "Back",
    ]


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


def test_project_pending_opportunity_does_not_depend_on_selected_brand(session: Session, monkeypatch):
    project = ProductionProject(
        source_url="https://youtu.be/ProjectScopedOpportunity",
        created_actor_id=uuid.UUID("a1111111-1111-1111-1111-111111111111"),
    )
    session.add(project)
    session.flush()
    analysis_id = uuid.uuid4()
    project_brand_id = uuid.uuid4()
    session.add(
        OpportunityGenerationRun(
            analysis_id=analysis_id,
            project_id=project.id,
            brand_id=project_brand_id,
            generation_version=1,
        )
    )
    opportunity = ClipOpportunity(
        analysis_id=analysis_id,
        project_id=project.id,
        brand_id=project_brand_id,
        generation_version=1,
        start_time=5.0,
        end_time=25.0,
        duration_seconds=20.0,
        confidence=0.8,
        overall_score=80.0,
        overlap_percentage=0.0,
        explanation="Project-scoped pending opportunity.",
    )
    rejected = ClipOpportunity(
        analysis_id=analysis_id,
        project_id=project.id,
        brand_id=project_brand_id,
        generation_version=1,
        start_time=30.0,
        end_time=50.0,
        duration_seconds=20.0,
        confidence=0.7,
        overall_score=70.0,
        overlap_percentage=0.0,
        review_status=OpportunityReviewStatus.REJECTED,
        explanation="Rejected project-scoped opportunity.",
    )
    session.add_all([opportunity, rejected])
    session.commit()

    def session_provider():  # type: ignore[no-untyped-def]
        yield session

    monkeypatch.setattr("app.discord_bot.get_session", session_provider)
    state = ProductionRepository(Settings()).first_pending_opportunity_for_project(project.id)
    assert state is not None
    assert state.opportunity.id == opportunity.id
    dashboard = ProductionRepository(Settings()).dashboard(project.id)
    assert dashboard.opportunity_count == 2
    assert dashboard.pending_opportunity_count == 1


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
        warnings_json=["Potentially sensitive content: persisted source title contains 'shooting'; review full context before use."],
    )
    repository = ProductionRepository(Settings())
    view = ContentPackageReviewView(package, repository, Settings(discord_allowed_role_ids="1"))
    embed = content_package_embed(package)
    assert len(view.children) == 5
    assert "Persisted transcript statement" in " ".join(field.value for field in embed.fields)
    assert "Sensitive-content review" in [field.name for field in embed.fields]


def test_content_package_review_looks_up_the_package_id(session: Session, monkeypatch):
    project_id = uuid.uuid4()
    package = ContentPackage(
        clip_id=uuid.uuid4(),
        project_id=project_id,
        generation_version=1,
        status="PENDING",
        review_version=1,
        provider_name="local_template",
        model_name="deterministic-template",
        provider_version="v1",
        language="en",
        content_category="SOURCE_CLIP",
        confidence=0.7,
        explanation="Evidence-bound package.",
        fields_json={},
        verified_facts_json=[],
        transcript_statements_json=[],
        uncertainty_json=[],
        warnings_json=[],
    )
    session.add(package)
    session.commit()

    def session_provider():  # type: ignore[no-untyped-def]
        yield session

    monkeypatch.setattr("app.discord_bot.get_session", session_provider)
    repository = ProductionRepository(Settings())
    assert repository.content_package_by_id(package.id) is not None
    assert repository.content_package_for_project(project_id) is not None


def test_producer_advice_view_is_concise_and_exposes_review_controls():
    project_id = uuid.uuid4()
    item = ProducerRecommendation(
        brand_id=uuid.uuid4(),
        project_id=project_id,
        recommendation_type="SOURCE_TRUST",
        confidence=0.6,
        reasoning="Persisted source-quality and watermark inspection support a careful review.",
        evidence_json=[{"note": "Persisted source-quality score.", "value": 70}],
        recommendation_json={"recommendation": "REVIEW_SOURCE"},
    )
    view = ProducerRecommendationView(project_id, [item], ProductionRepository(Settings()), Settings(discord_allowed_role_ids="1"))
    labels = {str(getattr(child, "label", "")) for child in view.children}
    assert {"Approve", "Reject", "Add / Edit Note", "More Details", "Back", "Home"} <= labels
    embed = producer_advice_embed(item, 0, 1)
    assert "Moderate (60%)" in next(field.value for field in embed.fields if field.name == "Confidence")
    assert "id" not in embed.description.lower()
