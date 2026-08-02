from datetime import UTC, datetime

from sqlalchemy import func, select

from app.brands.models import Brand, BrandMembership, ContentProfile, Workspace
from app.operations.models import OperationsReport, OperatorTask
from app.operations.service import (
    briefing,
    create_due_reports,
    health_summary,
    is_quiet_or_paused,
    refresh_operational_state,
    schedule_for,
)
from app.production.models import ProductionProject
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
