import uuid

import pytest

from app.accounts.auth import Actor, actor_can
from app.accounts.models import RoleName
from app.audit.models import AuditEvent
from app.common.errors import PreconditionError
from app.content.lifecycle import transition
from app.content.models import ContentItem, ContentStatus
from app.moderation.models import ModerationAssessment, ModerationDisposition, ModerationRisk
from app.review.models import ReviewDecision, ReviewOutcome
from app.rights.models import RightsAssessment, RightsDisposition, RightsState
from tests.conftest import DEV_ACTOR_ID


def test_approval_requires_manual_rights_moderation_and_review(session):  # type: ignore[no-untyped-def]
    item = ContentItem(
        title="x", source_provenance_complete=True, status=ContentStatus.REVIEW_REQUIRED
    )
    session.add(item)
    session.flush()
    actor_id = DEV_ACTOR_ID
    with pytest.raises(PreconditionError):
        transition(session, item, ContentStatus.APPROVED, actor_id, "approve")
    session.add(
        RightsAssessment(
            content_id=item.id,
            rights_state=RightsState.PERMISSION_GRANTED,
            disposition=RightsDisposition.APPROVED,
            is_automatic=False,
            policy_version="1",
            assessment_version="1",
        )
    )
    session.add(
        ModerationAssessment(
            content_id=item.id,
            risk_category=ModerationRisk.OTHER,
            disposition=ModerationDisposition.APPROVED,
            is_automatic=False,
            policy_version="1",
            assessment_version="1",
        )
    )
    session.add(
        ReviewDecision(
            content_id=item.id,
            reviewer_id=actor_id,
            outcome=ReviewOutcome.APPROVED,
            reason="looks good",
        )
    )
    session.flush()
    transition(session, item, ContentStatus.APPROVED, actor_id, "approved after review")
    assert item.status is ContentStatus.APPROVED
    assert session.query(AuditEvent).count() == 1


def test_automatic_assessment_is_not_approval(session):  # type: ignore[no-untyped-def]
    item = ContentItem(
        title="x", source_provenance_complete=True, status=ContentStatus.REVIEW_REQUIRED
    )
    session.add(item)
    session.flush()
    actor_id = DEV_ACTOR_ID
    session.add(
        RightsAssessment(
            content_id=item.id,
            rights_state=RightsState.LICENSED,
            disposition=RightsDisposition.APPROVED,
            is_automatic=True,
            policy_version="1",
            assessment_version="1",
        )
    )
    session.flush()
    with pytest.raises(PreconditionError):
        transition(session, item, ContentStatus.APPROVED, actor_id, "not legal approval")


def test_authorization_helper():
    actor = Actor(id=uuid.uuid4(), roles=frozenset({RoleName.REVIEWER}))
    assert actor_can(actor, RoleName.REVIEWER)
    assert not actor_can(actor, RoleName.ADMIN)
