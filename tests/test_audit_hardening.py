from datetime import UTC, datetime, timedelta

import pytest

from app.accounts.models import Role, RoleName, User, UserRole
from app.common.errors import PreconditionError
from app.content.lifecycle import transition
from app.content.models import ContentItem, ContentStatus
from app.moderation.models import ModerationAssessment, ModerationDisposition, ModerationRisk
from app.review.models import ReviewDecision, ReviewOutcome
from app.rights.models import RightsAssessment, RightsDisposition, RightsState
from tests.conftest import DEV_ACTOR_ID


def reviewed_item(session):  # type: ignore[no-untyped-def]
    item = ContentItem(
        title="audit", source_provenance_complete=True, status=ContentStatus.REVIEW_REQUIRED
    )
    session.add(item)
    session.flush()
    session.add_all(
        [
            ModerationAssessment(
                content_id=item.id,
                risk_category=ModerationRisk.OTHER,
                disposition=ModerationDisposition.APPROVED,
                is_automatic=False,
                policy_version="1",
                assessment_version="1",
            ),
            ReviewDecision(
                content_id=item.id,
                reviewer_id=DEV_ACTOR_ID,
                outcome=ReviewOutcome.APPROVED,
                reason="approved",
            ),
        ]
    )
    session.flush()
    return item


@pytest.mark.parametrize(
    "rights_state",
    [RightsState.DENIED, RightsState.DISPUTED, RightsState.RESTRICTED, RightsState.UNKNOWN],
)
def test_ineligible_manual_rights_cannot_approve(session, rights_state):  # type: ignore[no-untyped-def]
    item = reviewed_item(session)
    session.add(
        RightsAssessment(
            content_id=item.id,
            rights_state=rights_state,
            disposition=RightsDisposition.APPROVED,
            is_automatic=False,
            policy_version="1",
            assessment_version="1",
        )
    )
    session.flush()
    with pytest.raises(PreconditionError):
        transition(session, item, ContentStatus.APPROVED, DEV_ACTOR_ID, "audit")


def test_expired_rights_and_rejected_moderation_cannot_approve(session):  # type: ignore[no-untyped-def]
    item = reviewed_item(session)
    session.add_all(
        [
            RightsAssessment(
                content_id=item.id,
                rights_state=RightsState.LICENSED,
                disposition=RightsDisposition.APPROVED,
                is_automatic=False,
                policy_version="1",
                assessment_version="1",
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            ),
            ModerationAssessment(
                content_id=item.id,
                risk_category=ModerationRisk.GRAPHIC_VIOLENCE,
                disposition=ModerationDisposition.REJECTED,
                is_automatic=False,
                policy_version="1",
                assessment_version="1",
            ),
        ]
    )
    session.flush()
    with pytest.raises(PreconditionError):
        transition(session, item, ContentStatus.APPROVED, DEV_ACTOR_ID, "audit")


def test_system_reviewer_cannot_satisfy_human_approval(session):  # type: ignore[no-untyped-def]
    system_user = User(email="system@example.test", display_name="System")
    system_role = Role(name=RoleName.SYSTEM)
    session.add_all([system_user, system_role])
    session.flush()
    session.add(UserRole(user_id=system_user.id, role_id=system_role.id))
    item = ContentItem(
        title="audit", source_provenance_complete=True, status=ContentStatus.REVIEW_REQUIRED
    )
    session.add(item)
    session.flush()
    session.add_all(
        [
            RightsAssessment(
                content_id=item.id,
                rights_state=RightsState.LICENSED,
                disposition=RightsDisposition.APPROVED,
                is_automatic=False,
                policy_version="1",
                assessment_version="1",
            ),
            ModerationAssessment(
                content_id=item.id,
                risk_category=ModerationRisk.OTHER,
                disposition=ModerationDisposition.APPROVED,
                is_automatic=False,
                policy_version="1",
                assessment_version="1",
            ),
            ReviewDecision(
                content_id=item.id,
                reviewer_id=system_user.id,
                outcome=ReviewOutcome.APPROVED,
                reason="system",
            ),
        ]
    )
    session.flush()
    with pytest.raises(PreconditionError):
        transition(session, item, ContentStatus.APPROVED, DEV_ACTOR_ID, "audit")
