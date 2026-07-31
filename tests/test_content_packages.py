import uuid

from sqlalchemy import select

from app.analysis.models import AnalysisEvent, AnalysisStatus, TranscriptSegment, VideoAnalysis
from app.common.config import Settings
from app.content_packages.models import ContentPackage, ContentPackageStatus, ContentPackageVersion
from app.content_packages.service import (
    decide_content_package,
    edit_content_package,
    execute_content_package_generation,
    request_content_package_generation,
)
from app.opportunities.models import ClipOpportunity, OpportunityReason
from app.production.models import (
    PostingQueueItem,
    ProductionClip,
    ProductionProject,
    ProductionSource,
)
from tests.conftest import DEV_ACTOR_ID


def _rendered_clip(session):  # type: ignore[no-untyped-def]
    project = ProductionProject(
        source_url=f"https://www.youtube.com/watch?v={uuid.uuid4().hex[:11]}",
        source_title="Persisted source title",
        source_channel="Official Source",
        source_duration_seconds=60.0,
        status="SOURCE_READY",
        created_actor_id=DEV_ACTOR_ID,
    )
    session.add(project)
    session.flush()
    source = ProductionSource(
        project_id=project.id,
        source_url=project.source_url,
        uploader_name="Official Source",
        video_title=project.source_title,
        warnings=["Source context must be reviewed."],
    )
    session.add(source)
    session.flush()
    project.selected_source_id = source.id
    clip = ProductionClip(
        project_id=project.id,
        clip_number=1,
        start_seconds=10.0,
        end_seconds=30.0,
        duration_seconds=20.0,
        storage_key="clips/rendered.mp4",
        render_status="SUCCEEDED",
        publication_status="NOT_QUEUED",
    )
    analysis = VideoAnalysis(
        project_id=project.id,
        source_id=source.id,
        status=AnalysisStatus.COMPLETED,
        duration_seconds=60.0,
        transcript_language="en",
    )
    session.add_all([clip, analysis])
    session.flush()
    session.add(TranscriptSegment(
        analysis_id=analysis.id,
        start_time=12.0,
        end_time=18.0,
        text="The persisted transcript says this is the source statement.",
        confidence=0.95,
        metadata_json={},
    ))
    session.add(AnalysisEvent(
        analysis_id=analysis.id,
        timestamp=15.0,
        event_type="SHOT_CHANGE",
        confidence=0.8,
        metadata_json={},
    ))
    opportunity = ClipOpportunity(
        analysis_id=analysis.id,
        project_id=project.id,
        generation_version=1,
        start_time=10.0,
        end_time=30.0,
        duration_seconds=20.0,
        confidence=0.8,
        overall_score=80.0,
        explanation="Persisted opportunity explanation.",
        generated_clip_id=clip.id,
    )
    session.add(opportunity)
    session.flush()
    session.add(OpportunityReason(
        opportunity_id=opportunity.id,
        reason_type="TRANSCRIPT_CONFIDENCE",
        score=0.9,
        weight=0.15,
        metadata_json={},
    ))
    session.commit()
    return clip


def test_content_package_is_evidence_bound_versioned_and_never_queues(session):  # type: ignore[no-untyped-def]
    clip = _rendered_clip(session)
    queued = request_content_package_generation(session, DEV_ACTOR_ID, clip)
    assert queued.status == ContentPackageStatus.QUEUED
    package = execute_content_package_generation(session, DEV_ACTOR_ID, queued, settings=Settings())
    assert package.status == ContentPackageStatus.PENDING
    assert package.fields_json["content_category"] == "SOURCE_CLIP"
    assert "persisted transcript says" in " ".join(package.transcript_statements_json).lower()
    assert package.verified_facts_json and package.uncertainty_json
    assert package.fields_json["source_attribution_text"] == "Source: Official Source — Persisted source title"
    assert session.scalar(select(PostingQueueItem).where(PostingQueueItem.clip_id == clip.id)) is None
    assert request_content_package_generation(session, DEV_ACTOR_ID, clip).id == package.id
    assert len(list(session.scalars(select(ContentPackageVersion)))) == 2


def test_edit_optimistic_lock_approval_and_regeneration(session):  # type: ignore[no-untyped-def]
    clip = _rendered_clip(session)
    queued = request_content_package_generation(session, DEV_ACTOR_ID, clip)
    package = execute_content_package_generation(session, DEV_ACTOR_ID, queued, settings=Settings())
    fields = dict(package.fields_json)
    fields["youtube_shorts_title"] = "Operator-approved title"
    edited = edit_content_package(session, DEV_ACTOR_ID, package, package.review_version, fields)
    assert edited.status == ContentPackageStatus.PENDING
    approved = decide_content_package(session, DEV_ACTOR_ID, edited, edited.review_version, True)
    assert approved.status == ContentPackageStatus.APPROVED
    assert session.scalar(select(PostingQueueItem).where(PostingQueueItem.clip_id == clip.id)) is None
    rerun = request_content_package_generation(session, DEV_ACTOR_ID, clip, rerun=True)
    assert rerun.generation_version == 2
    assert approved.status == ContentPackageStatus.STALE
    assert len(list(session.scalars(select(ContentPackage).where(ContentPackage.clip_id == clip.id)))) == 2


def test_content_package_api_queues_edits_and_lists_versions(client, session):  # type: ignore[no-untyped-def]
    clip = _rendered_clip(session)
    headers = {"X-Development-Actor": str(DEV_ACTOR_ID)}
    queued = client.post(f"/api/v1/production/clips/{clip.id}/content-packages", headers=headers)
    assert queued.status_code == 202, queued.text
    package = session.get(ContentPackage, uuid.UUID(queued.json()["id"]))
    assert package is not None
    package = execute_content_package_generation(session, DEV_ACTOR_ID, package, settings=Settings())
    listed = client.get(f"/api/v1/production/clips/{clip.id}/content-packages", headers=headers)
    assert listed.status_code == 200 and listed.json()[0]["fields_json"]["primary_hook"]
    edited = client.patch(
        f"/api/v1/content-packages/{package.id}",
        headers=headers,
        json={"expected_version": package.review_version, "fields_json": {"primary_hook": "Edited"}},
    )
    assert edited.status_code == 200, edited.text
    versions = client.get(f"/api/v1/content-packages/{package.id}/versions", headers=headers)
    assert versions.status_code == 200 and len(versions.json()) == 3
