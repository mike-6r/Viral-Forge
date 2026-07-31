import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base
from app.common.types import UUIDTimestampMixin


class RightsState(StrEnum):
    UNKNOWN = "UNKNOWN"
    OWNER_SUBMITTED = "OWNER_SUBMITTED"
    LICENSED = "LICENSED"
    PUBLIC_DOMAIN = "PUBLIC_DOMAIN"
    PERMISSION_GRANTED = "PERMISSION_GRANTED"
    PLATFORM_REUSE_ALLOWED = "PLATFORM_REUSE_ALLOWED"
    ATTRIBUTION_REQUIRED = "ATTRIBUTION_REQUIRED"
    RESTRICTED = "RESTRICTED"
    DISPUTED = "DISPUTED"
    DENIED = "DENIED"


class RightsDisposition(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"


class RightsAssessment(UUIDTimestampMixin, Base):
    __tablename__ = "rights_assessments"
    content_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_items.id"), index=True
    )
    rights_state: Mapped[RightsState] = mapped_column(Enum(RightsState))
    disposition: Mapped[RightsDisposition] = mapped_column(
        Enum(RightsDisposition), default=RightsDisposition.PENDING
    )
    is_automatic: Mapped[bool] = mapped_column(default=False)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    reviewer_notes: Mapped[str | None] = mapped_column(Text)
    evidence_reference: Mapped[str | None] = mapped_column(String(2048))
    policy_version: Mapped[str] = mapped_column(String(100))
    assessment_version: Mapped[str] = mapped_column(String(100))
    attribution_instructions: Mapped[str | None] = mapped_column(Text)
    usage_restrictions: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    geographic_restrictions: Mapped[str | None] = mapped_column(Text)
    platform_restrictions: Mapped[str | None] = mapped_column(Text)
