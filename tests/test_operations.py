import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.brands.models import Brand, BrandMembership, ContentProfile, Workspace
from app.content_packages.models import ContentPackage, ContentPackageStatus
from app.discovery.models import DiscoveredMedia, DiscoverySource, DiscoveryStatus
from app.operations.models import OperationsReport, OperatorTask
from app.operations.service import (
    briefing,
    create_due_reports,
    health_summary,
    is_quiet_or_paused,
    next_review_item,
    refresh_operational_state,
    review_inbox_counts,
    schedule_for,
)
from app.opportunities.models import ClipOpportunity, OpportunityReviewStatus
from app.production.models import ProductionClip, ProductionProject
from tests.conftest import DEV_ACTOR_ID


def _brand(session, name: str = "Operations"):
    workspace = Workspace(name=f"{name} Workspace", slug=f"{name.lower()}-workspace")
    session.add(workspace)
    session.flush()
    brand = Brand(workspace_id=workspace.id, name=name, slug=name.lower())
    session.add(brand)
    session.flush()
    session.add(BrandMembership(brand_id=brand.id, user_id=DEV_ACTOR_ID, role="ADMIN", is_default=True))
    session.add(ContentProfile(brand_id=brand.id, niche_name=name, timezone="America/New_York"))
    session.commit()
    return brand


def test_schedule_honors_timezone_quiet_hours_and_holidays(session):
    brand = _brand(session)
    profile = session.scalar(select(ContentProfile).where(ContentProfile.brand_id == brand.id))
    assert profile is not None
    profile.operations_schedule_json = {"quiet_hours": [{"start": "22:00", "end": "06:00"}], "holidays": ["2026-12-25"]}
    session.commit()
    config = schedule_for(session, brand.id)
    assert is_quiet_or_paused(config, datetime(2026, 12, 25, 17, tzinfo=UTC))
    assert is_quiet_or_paused(config, datetime(2026, 8, 3, 3, tzinfo=UTC))
    assert not is_quiet_or_paused(config, datetime(2026, 8, 3, 16, tzinfo=UTC))


def test_operational_tasks_are_grouped_and_brand_scoped(session):
    brand_a = _brand(session, "Alpha")
    brand_b = _brand(session, "Beta")
    session.add(ProductionProject(brand_id=brand_a.id, source_url="https://example.test/failed-a", status="DOWNLOAD_FAILED", created_actor_id=DEV_ACTOR_ID))
    session.commit()
    refresh_operational_state(session, brand_a.id)
    refresh_operational_state(session, brand_a.id)
    tasks = list(session.scalars(select(OperatorTask).where(OperatorTask.brand_id == brand_a.id)))
    assert {task.task_type for task in tasks} >= {"RETRY_FAILED", "CONNECT_DESTINATION"}
    assert session.scalar(select(func.count()).select_from(OperatorTask).where(OperatorTask.brand_id == brand_a.id, OperatorTask.task_type == "RETRY_FAILED")) == 1
    assert session.scalar(select(func.count()).select_from(OperatorTask).where(OperatorTask.brand_id == brand_b.id)) == 0


def test_briefing_and_health_do_not_create_publish_work(session):
    brand = _brand(session)
    summary = health_summary(session, brand.id)
    report = briefing(session, brand.id)
    assert summary["state"] == "Healthy"
    assert report["videos_found"] == 0
    assert report["content_ready"] == 0


def test_review_inbox_is_complete_brand_scoped_and_matches_health(session):
    brand = _brand(session, "Review Alpha")
    other_brand = _brand(session, "Review Beta")
    project = ProductionProject(
        brand_id=brand.id,
        source_url="https://example.test/review-source",
        status="SOURCE_REVIEW_REQUIRED",
        created_actor_id=DEV_ACTOR_ID,
    )
    other_project = ProductionProject(
        brand_id=other_brand.id,
        source_url="https://example.test/other-source",
        status="SOURCE_REVIEW_REQUIRED",
        created_actor_id=DEV_ACTOR_ID,
    )
    session.add_all([project, other_project])
    session.flush()
    clip = ProductionClip(
        project_id=project.id,
        brand_id=brand.id,
        clip_number=1,
        start_seconds=0,
        end_seconds=30,
        duration_seconds=30,
        render_status="SUCCEEDED",
        approval_status="PENDING",
    )
    source = DiscoverySource(
        brand_id=brand.id,
        name="Review source",
        provider="YOUTUBE",
        source_type="CHANNEL",
        platform="YOUTUBE",
        public_url="https://example.test/review-channel",
    )
    session.add_all([clip, source])
    session.flush()
    session.add_all(
        [
            ClipOpportunity(
                analysis_id=uuid.uuid4(),
                project_id=project.id,
                brand_id=brand.id,
                start_time=0,
                end_time=30,
                duration_seconds=30,
                confidence=0.8,
                overall_score=80,
                explanation="Pending opportunity",
                review_status=OpportunityReviewStatus.PENDING,
            ),
            ContentPackage(
                clip_id=clip.id,
                project_id=project.id,
                brand_id=brand.id,
                provider_name="local",
                explanation="Pending package",
                status=ContentPackageStatus.PENDING,
            ),
            DiscoveredMedia(
                discovery_source_id=source.id,
                brand_id=brand.id,
                provider_item_id="review-media",
                canonical_url="https://example.test/review-media",
                submitted_url="https://example.test/review-media",
                platform="YOUTUBE",
                discovered_at=datetime.now(UTC),
                lifecycle_status=DiscoveryStatus.NEEDS_REVIEW,
            ),
        ]
    )
    session.commit()

    counts = review_inbox_counts(session, brand.id)
    assert counts == {
        "source": 1,
        "opportunity": 1,
        "clip": 1,
        "content_package": 1,
        "discovery": 1,
        "total": 5,
    }
    assert health_summary(session, brand.id)["review_items"] == counts["total"]
    first = next_review_item(session, brand.id)
    assert first is not None and first.kind == "SOURCE" and first.id == project.id
    assert review_inbox_counts(session, other_brand.id)["total"] == 1


def test_refresh_closes_stale_review_task_when_no_review_work_remains(session):
    brand = _brand(session, "No Review Work")
    stale_task = OperatorTask(
        brand_id=brand.id,
        priority="HIGH",
        task_type="REVIEW_CONTENT",
        dedupe_key="review_content",
        title="Review content",
        reason="1 item need a creative decision.",
        action_label="Review Content",
    )
    session.add(stale_task)
    session.commit()

    refresh_operational_state(session, brand.id)
    session.refresh(stale_task)
    assert stale_task.status == "COMPLETED"
    assert stale_task.completed_at is not None
    assert not [task for task in briefing(session, brand.id)["attention"] if task.task_type == "REVIEW_CONTENT"]


def test_daily_reports_are_deduplicated_and_respect_quiet_hours(session):
    brand = _brand(session)
    profile = session.scalar(select(ContentProfile).where(ContentProfile.brand_id == brand.id))
    assert profile is not None
    profile.operations_schedule_json = {
        "morning_briefing_hour": 9,
        "evening_report_hour": 18,
        "quiet_hours": [{"start": "22:00", "end": "06:00"}],
    }
    session.commit()
    afternoon_utc = datetime(2026, 8, 3, 23, tzinfo=UTC)  # 19:00 America/New_York
    assert create_due_reports(session, brand.id, afternoon_utc) == 2
    assert create_due_reports(session, brand.id, afternoon_utc) == 0
    assert session.scalar(select(func.count()).select_from(OperationsReport).where(OperationsReport.brand_id == brand.id)) == 2


def test_operations_api_is_brand_scoped_and_requires_an_actor(client, session):  # type: ignore[no-untyped-def]
    brand = _brand(session)
    headers = {"X-Development-Actor": str(DEV_ACTOR_ID)}
    schedule_response = client.patch(
        f"/api/v1/brands/{brand.id}/content-profile",
        headers=headers,
        json={"operations_schedule_json": {"quiet_hours": [{"start": "22:00", "end": "06:00"}]}},
    )
    briefing_response = client.get(f"/api/v1/operations/briefing?brand_id={brand.id}", headers=headers)
    health_response = client.get(f"/api/v1/operations/health?brand_id={brand.id}", headers=headers)
    refresh_response = client.post(f"/api/v1/operations/refresh?brand_id={brand.id}", headers=headers)
    assert schedule_response.status_code == 200
    assert briefing_response.status_code == 200
    assert health_response.status_code == 200
    assert refresh_response.status_code == 200
    assert briefing_response.json()["brand"] == brand.name
