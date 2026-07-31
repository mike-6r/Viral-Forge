import uuid
from enum import StrEnum

from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base
from app.common.types import UUIDTimestampMixin


class ReviewOutcome(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ReviewDecision(UUIDTimestampMixin, Base):
    __tablename__ = "review_decisions"
    content_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_items.id"), index=True
    )
    reviewer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), index=True
    )
    outcome: Mapped[ReviewOutcome] = mapped_column(Enum(ReviewOutcome))
    reason: Mapped[str] = mapped_column(Text)
    approval_type: Mapped[str] = mapped_column(String(100), default="HUMAN_CONTENT_APPROVAL")
