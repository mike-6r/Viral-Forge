import uuid

import pytest

from app.brands.service import ensure_legacy_brand
from app.corrections.models import CorrectionPlanStatus
from app.corrections.service import (
    CorrectionError,
    create_plan,
    set_action_selected,
    submit_for_confirmation,
    validate_plan,
)
from app.production.models import ProductionClip, ProductionProject
from app.rendered_media.models import (
    RenderedMediaInspection,
    RenderedMediaInspectionIssue,
    RenderedMediaInspectionStatus,
)
from tests.conftest import DEV_ACTOR_ID


def _clip_with_inspection(session):  # type: ignore[no-untyped-def]
    brand = ensure_legacy_brand(session)
    project = ProductionProject(
        brand_id=brand.id, source_url=f"https://example.test/correction/{uuid.uuid4()}",
        source_duration_seconds=20.0, status="CLIPS_READY", created_actor_id=DEV_ACTOR_ID,
    )
    session.add(project); session.flush()
    clip = ProductionClip(project_id=project.id, brand_id=brand.id, clip_number=1, start_seconds=0.0, end_seconds=20.0, duration_seconds=20.0, storage_key="assets/controlled.mp4", render_status="SUCCEEDED")
    session.add(clip); session.flush()
    inspection = RenderedMediaInspection(brand_id=brand.id, project_id=project.id, clip_id=clip.id, inspection_version=1, status=RenderedMediaInspectionStatus.COMPLETED, overall_score=50.0, confidence=0.8)
    session.add(inspection); session.flush()
    session.add(RenderedMediaInspectionIssue(inspection_id=inspection.id, issue_type="OPENING_BLACK_FRAMES", severity="HIGH", start_seconds=0.0, end_seconds=1.5, explanation="black opening", recommendation="trim", confidence=0.9))
    session.commit()
    return clip


def test_plan_is_evidence_bound_and_requires_explicit_confirmation(session):  # type: ignore[no-untyped-def]
    clip = _clip_with_inspection(session)
    plan = create_plan(session, DEV_ACTOR_ID, clip)
    assert plan.status == CorrectionPlanStatus.DRAFT
    assert validate_plan(session, plan) == []
    awaiting = submit_for_confirmation(session, DEV_ACTOR_ID, plan, plan.review_version)
    assert awaiting.status == CorrectionPlanStatus.AWAITING_CONFIRMATION
    assert session.get(ProductionClip, clip.id).storage_key == "assets/controlled.mp4"
    assert session.get(ProductionClip, clip.id).approval_status == "PENDING"


def test_action_locking_and_bounds_are_safe(session):  # type: ignore[no-untyped-def]
    clip = _clip_with_inspection(session)
    plan = create_plan(session, DEV_ACTOR_ID, clip)
    action = next(iter(plan.__dict__.get("actions", []) or []), None)
    # Load via service-selected API shape; stale versions cannot change a plan.
    from app.corrections.service import actions

    action = actions(session, plan)[0]
    with pytest.raises(CorrectionError, match="no longer editable"):
        set_action_selected(session, DEV_ACTOR_ID, plan, action.id, False, plan.review_version + 1)
