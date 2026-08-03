from sqlalchemy import func, select

from app.autopilot.models import AutopilotDecision, AutopilotException, AutopilotScheduleSlot
from app.autopilot.service import decide, policy_for, reserve_slot, update_policy
from app.brands.models import Brand, BrandMembership, ContentProfile, DestinationAccount, Workspace
from app.content_packages.models import ContentPackage
from app.production.models import PostingQueueItem, ProductionClip, ProductionProject
from tests.conftest import DEV_ACTOR_ID


def _brand(session, slug: str):  # type: ignore[no-untyped-def]
    workspace = Workspace(name=f"{slug} workspace", slug=f"{slug}-workspace")
    session.add(workspace)
    session.flush()
    brand = Brand(workspace_id=workspace.id, name=slug, slug=slug)
    session.add(brand)
    session.flush()
    session.add_all(
        [
            BrandMembership(brand_id=brand.id, user_id=DEV_ACTOR_ID, role="ADMIN", is_default=True),
            ContentProfile(brand_id=brand.id, niche_name=slug),
        ]
    )
    session.commit()
    return brand


def _safe_config():
    return {
        "source": {
            "automatic_source_acceptance_enabled": True,
            "require_rights": True,
            "require_moderation": True,
            "minimum_trust": 0.8,
        },
        "schedule": {
            "enabled": True,
            "timezone": "UTC",
            "maximum_posts_per_day": 2,
            "minimum_spacing_minutes": 60,
        },
        "publishing": {
            "automatic_transfer_enabled": False,
            "require_final_human_confirmation": True,
        },
    }


def test_manual_policy_fails_closed_and_creates_one_grouped_exception(session):  # type: ignore[no-untyped-def]
    brand = _brand(session, "manual")
    result = decide(session, brand.id, "PROCESS_SOURCE", "project", "project-a")
    again = decide(session, brand.id, "PROCESS_SOURCE", "project", "project-a")
    assert result.decision == "REQUIRE_REVIEW"
    assert again.decision == "REQUIRE_REVIEW"
    assert session.scalar(select(func.count()).select_from(AutopilotDecision)) == 2
    assert session.scalar(select(func.count()).select_from(AutopilotException)) == 1


def test_supervised_policy_requires_rights_moderation_and_threshold_evidence(session):  # type: ignore[no-untyped-def]
    brand = _brand(session, "supervised")
    policy = policy_for(session, brand.id, create=True)
    assert policy is not None
    policy = update_policy(
        session,
        brand.id,
        DEV_ACTOR_ID,
        policy.version,
        _safe_config(),
        automation_level="SUPERVISED_AUTOPILOT",
    )
    missing = decide(session, brand.id, "PROCESS_SOURCE", "project", "one", evidence={})
    allowed = decide(
        session,
        brand.id,
        "PROCESS_SOURCE",
        "project",
        "two",
        evidence={"rights_approved": True, "moderation_approved": True, "source_trust": 1},
    )
    assert missing.decision == "REQUIRE_REVIEW"
    assert "MISSING_EVIDENCE" in missing.record.reason_codes
    assert allowed.decision == "ALLOW"
    assert allowed.record.policy_version == policy.version


def test_policy_version_conflict_and_cross_brand_schedule_are_rejected(session):  # type: ignore[no-untyped-def]
    alpha = _brand(session, "alpha-auto")
    beta = _brand(session, "beta-auto")
    policy = policy_for(session, alpha.id, create=True)
    assert policy is not None
    stale_version = policy.version
    update_policy(
        session,
        alpha.id,
        DEV_ACTOR_ID,
        stale_version,
        _safe_config(),
        automation_level="SUPERVISED_AUTOPILOT",
    )
    try:
        update_policy(session, alpha.id, DEV_ACTOR_ID, stale_version, _safe_config())
    except ValueError as error:
        assert "version conflict" in str(error)
    else:
        raise AssertionError("stale policy update was accepted")
    project = ProductionProject(
        brand_id=alpha.id,
        source_url="https://example.test/autopilot",
        created_actor_id=DEV_ACTOR_ID,
    )
    session.add(project)
    session.flush()
    clip = ProductionClip(
        project_id=project.id,
        brand_id=alpha.id,
        clip_number=1,
        start_seconds=0,
        end_seconds=30,
        duration_seconds=30,
        render_status="SUCCEEDED",
        approval_status="APPROVED",
    )
    session.add(clip)
    session.flush()
    queue = PostingQueueItem(clip_id=clip.id, brand_id=alpha.id)
    package = ContentPackage(
        clip_id=clip.id,
        project_id=project.id,
        brand_id=alpha.id,
        provider_name="local",
        explanation="test",
    )
    destination = DestinationAccount(
        brand_id=beta.id, provider="YOUTUBE", account_reference="other"
    )
    session.add_all([queue, package, destination])
    session.commit()
    try:
        reserve_slot(
            session, alpha.id, queue.id, destination.id, package.id, "2026-08-05T12:00:00+00:00"
        )
    except ValueError as error:
        assert "same brand" in str(error)
    else:
        raise AssertionError("cross-brand destination was accepted")


def test_schedule_reservation_is_idempotent_and_never_creates_publish_request(session):  # type: ignore[no-untyped-def]
    brand = _brand(session, "schedule")
    policy = policy_for(session, brand.id, create=True)
    assert policy is not None
    update_policy(
        session,
        brand.id,
        DEV_ACTOR_ID,
        policy.version,
        _safe_config(),
        automation_level="SUPERVISED_AUTOPILOT",
    )
    project = ProductionProject(
        brand_id=brand.id, source_url="https://example.test/schedule", created_actor_id=DEV_ACTOR_ID
    )
    session.add(project)
    session.flush()
    clip = ProductionClip(
        project_id=project.id,
        brand_id=brand.id,
        clip_number=1,
        start_seconds=0,
        end_seconds=30,
        duration_seconds=30,
        render_status="SUCCEEDED",
        approval_status="APPROVED",
    )
    session.add(clip)
    session.flush()
    queue = PostingQueueItem(clip_id=clip.id, brand_id=brand.id)
    package = ContentPackage(
        clip_id=clip.id,
        project_id=project.id,
        brand_id=brand.id,
        provider_name="local",
        explanation="test",
    )
    destination = DestinationAccount(
        brand_id=brand.id, provider="YOUTUBE", account_reference="owned"
    )
    session.add_all([queue, package, destination])
    session.commit()
    first = reserve_slot(
        session, brand.id, queue.id, destination.id, package.id, "2026-08-05T12:00:00+00:00"
    )
    second = reserve_slot(
        session, brand.id, queue.id, destination.id, package.id, "2026-08-05T12:00:00+00:00"
    )
    assert first.id == second.id
    assert first.confirmation_required
    assert session.scalar(select(func.count()).select_from(AutopilotScheduleSlot)) == 1


def test_autopilot_api_is_brand_scoped_and_keeps_safeguards(client, session):  # type: ignore[no-untyped-def]
    brand = _brand(session, "api-autopilot")
    headers = {"X-Development-Actor": str(DEV_ACTOR_ID)}
    current = client.get(f"/api/v1/autopilot/policy?brand_id={brand.id}", headers=headers)
    assert current.status_code == 200
    updated = client.put(
        f"/api/v1/autopilot/policy?brand_id={brand.id}",
        headers=headers,
        json={
            "expected_version": current.json()["version"],
            "automation_level": "SUPERVISED_AUTOPILOT",
            "config_json": _safe_config(),
        },
    )
    assert updated.status_code == 200
    preview = client.post(
        f"/api/v1/autopilot/preview?brand_id={brand.id}",
        headers=headers,
        json={"action": "DIRECT_POST", "object_type": "clip", "object_id": "safe-test"},
    )
    assert preview.status_code == 200
    assert preview.json()["decision"] == "BLOCK"
