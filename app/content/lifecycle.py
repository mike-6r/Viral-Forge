import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts.models import Role, RoleName, UserRole
from app.audit.models import AuditEvent
from app.common.errors import InvalidTransitionError, PreconditionError
from app.content.models import ContentItem, ContentStatus
from app.moderation.models import ModerationAssessment, ModerationDisposition
from app.review.models import ReviewDecision, ReviewOutcome
from app.rights.models import RightsAssessment, RightsDisposition, RightsState

TRANSITIONS: dict[ContentStatus, set[ContentStatus]] = {
    ContentStatus.DISCOVERED: {
        ContentStatus.IMPORTED,
        ContentStatus.ARCHIVED,
        ContentStatus.BLOCKED,
    },
    ContentStatus.IMPORTED: {
        ContentStatus.SOURCE_VERIFICATION_REQUIRED,
        ContentStatus.ARCHIVED,
        ContentStatus.BLOCKED,
    },
    ContentStatus.SOURCE_VERIFICATION_REQUIRED: {
        ContentStatus.RIGHTS_REVIEW_REQUIRED,
        ContentStatus.BLOCKED,
    },
    ContentStatus.RIGHTS_REVIEW_REQUIRED: {
        ContentStatus.MODERATION_REQUIRED,
        ContentStatus.BLOCKED,
    },
    ContentStatus.MODERATION_REQUIRED: {ContentStatus.READY_FOR_RANKING, ContentStatus.BLOCKED},
    ContentStatus.READY_FOR_RANKING: {ContentStatus.RANKED, ContentStatus.BLOCKED},
    ContentStatus.RANKED: {
        ContentStatus.PROCESSING_QUEUED,
        ContentStatus.ARCHIVED,
        ContentStatus.BLOCKED,
    },
    ContentStatus.PROCESSING_QUEUED: {ContentStatus.PROCESSING, ContentStatus.BLOCKED},
    ContentStatus.PROCESSING: {ContentStatus.REVIEW_REQUIRED, ContentStatus.BLOCKED},
    ContentStatus.REVIEW_REQUIRED: {
        ContentStatus.APPROVED,
        ContentStatus.REJECTED,
        ContentStatus.BLOCKED,
    },
    ContentStatus.APPROVED: {
        ContentStatus.SCHEDULED,
        ContentStatus.ARCHIVED,
        ContentStatus.BLOCKED,
    },
    ContentStatus.REJECTED: {ContentStatus.ARCHIVED},
    ContentStatus.SCHEDULED: {
        ContentStatus.PUBLISHING,
        ContentStatus.ARCHIVED,
        ContentStatus.BLOCKED,
    },
    ContentStatus.PUBLISHING: {
        ContentStatus.PUBLISHED,
        ContentStatus.PUBLISH_FAILED,
        ContentStatus.BLOCKED,
    },
    ContentStatus.PUBLISH_FAILED: {
        ContentStatus.SCHEDULED,
        ContentStatus.ARCHIVED,
        ContentStatus.BLOCKED,
    },
    ContentStatus.PUBLISHED: {ContentStatus.ARCHIVED},
    ContentStatus.ARCHIVED: set(),
    ContentStatus.BLOCKED: {ContentStatus.ARCHIVED},
}
RESTRICTED_TARGETS = {
    ContentStatus.APPROVED,
    ContentStatus.SCHEDULED,
    ContentStatus.PUBLISHING,
    ContentStatus.PUBLISHED,
}


ELIGIBLE_RIGHTS_STATES = {
    RightsState.OWNER_SUBMITTED,
    RightsState.LICENSED,
    RightsState.PUBLIC_DOMAIN,
    RightsState.PERMISSION_GRANTED,
    RightsState.PLATFORM_REUSE_ALLOWED,
    RightsState.ATTRIBUTION_REQUIRED,
}
HUMAN_APPROVER_ROLES = {RoleName.OWNER, RoleName.ADMIN, RoleName.REVIEWER}


def _latest_manual_rights(session: Session, content_id: uuid.UUID) -> RightsAssessment | None:
    return session.scalar(
        select(RightsAssessment)
        .where(RightsAssessment.content_id == content_id, RightsAssessment.is_automatic.is_(False))
        .order_by(RightsAssessment.created_at.desc(), RightsAssessment.id.desc())
        .limit(1)
    )


def _manual_moderation_is_approved(session: Session, content_id: uuid.UUID) -> bool:
    rejected = session.scalar(
        select(ModerationAssessment.id)
        .where(
            ModerationAssessment.content_id == content_id,
            ModerationAssessment.is_automatic.is_(False),
            ModerationAssessment.disposition == ModerationDisposition.REJECTED,
        )
        .limit(1)
    )
    if rejected is not None:
        return False
    return (
        session.scalar(
            select(ModerationAssessment.id)
            .where(
                ModerationAssessment.content_id == content_id,
                ModerationAssessment.is_automatic.is_(False),
                ModerationAssessment.disposition == ModerationDisposition.APPROVED,
            )
            .limit(1)
        )
        is not None
    )


def _rights_are_approved(session: Session, content_id: uuid.UUID) -> bool:
    assessment = _latest_manual_rights(session, content_id)
    if (
        assessment is None
        or assessment.disposition is not RightsDisposition.APPROVED
        or assessment.rights_state not in ELIGIBLE_RIGHTS_STATES
    ):
        return False
    if assessment.expires_at is None:
        return True
    expires_at = assessment.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at > datetime.now(UTC)


def _human_review_exists(session: Session, content_id: uuid.UUID) -> bool:
    statement = (
        select(ReviewDecision.id)
        .join(UserRole, UserRole.user_id == ReviewDecision.reviewer_id)
        .join(Role, Role.id == UserRole.role_id)
        .where(
            ReviewDecision.content_id == content_id,
            ReviewDecision.outcome == ReviewOutcome.APPROVED,
            Role.name.in_(HUMAN_APPROVER_ROLES),
        )
        .limit(1)
    )
    return session.scalar(statement) is not None


def validate_preconditions(session: Session, item: ContentItem, target: ContentStatus) -> None:
    if target in RESTRICTED_TARGETS:
        if not _rights_are_approved(session, item.id):
            raise PreconditionError("a current eligible manual rights approval is required")
        if not _manual_moderation_is_approved(session, item.id):
            raise PreconditionError("a manual approved moderation assessment is required")
    if target is ContentStatus.APPROVED:
        if not _human_review_exists(session, item.id):
            raise PreconditionError("a separate human approval record is required")
    if target is ContentStatus.RIGHTS_REVIEW_REQUIRED and not item.source_provenance_complete:
        raise PreconditionError("source provenance must be complete before rights review")


def transition(
    session: Session,
    item: ContentItem,
    target: ContentStatus,
    actor_id: uuid.UUID,
    reason: str,
    correlation_id: str | None = None,
) -> ContentItem:
    if target not in TRANSITIONS[item.status]:
        raise InvalidTransitionError(f"cannot transition {item.status} to {target}")
    validate_preconditions(session, item, target)
    prior = item.status
    item.status = target
    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="content_item",
            entity_id=item.id,
            event_name="content.transitioned",
            reason=reason,
            correlation_id=correlation_id,
            payload={"from": prior.value, "to": target.value},
        )
    )
    session.flush()
    return item
