import uuid
from enum import StrEnum

from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base
from app.common.types import UUIDTimestampMixin


class ModerationRisk(StrEnum):
    GRAPHIC_VIOLENCE = "GRAPHIC_VIOLENCE"
    DEATH_OR_SERIOUS_INJURY = "DEATH_OR_SERIOUS_INJURY"
    MINORS = "MINORS"
    PERSONAL_INFORMATION = "PERSONAL_INFORMATION"
    SEXUAL_CONTENT = "SEXUAL_CONTENT"
    HATE_OR_HARASSMENT = "HATE_OR_HARASSMENT"
    SELF_HARM = "SELF_HARM"
    ILLEGAL_ACTIVITY = "ILLEGAL_ACTIVITY"
    ACTIVE_INVESTIGATION = "ACTIVE_INVESTIGATION"
    MISLEADING_CONTEXT = "MISLEADING_CONTEXT"
    SENSITIVE_LOCATION = "SENSITIVE_LOCATION"
    OTHER = "OTHER"


class ModerationDisposition(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"


class ModerationAssessment(UUIDTimestampMixin, Base):
    __tablename__ = "moderation_assessments"
    content_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_items.id"), index=True
    )
    risk_category: Mapped[ModerationRisk] = mapped_column(Enum(ModerationRisk))
    disposition: Mapped[ModerationDisposition] = mapped_column(
        Enum(ModerationDisposition), default=ModerationDisposition.PENDING
    )
    is_automatic: Mapped[bool] = mapped_column(default=False)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    reviewer_notes: Mapped[str | None] = mapped_column(Text)
    evidence_reference: Mapped[str | None] = mapped_column(String(2048))
    policy_version: Mapped[str] = mapped_column(String(100))
    assessment_version: Mapped[str] = mapped_column(String(100))
