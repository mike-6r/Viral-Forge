import uuid
from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base
from app.common.types import UUIDTimestampMixin


class RankingAssessment(UUIDTimestampMixin, Base):
    __tablename__ = "ranking_assessments"
    content_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_items.id"), index=True
    )
    platform_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("platform_accounts.id")
    )
    engagement_score: Mapped[float | None] = mapped_column(Float)
    velocity_score: Mapped[float | None] = mapped_column(Float)
    freshness_score: Mapped[float | None] = mapped_column(Float)
    topic_score: Mapped[float | None] = mapped_column(Float)
    quality_score: Mapped[float | None] = mapped_column(Float)
    clipability_score: Mapped[float | None] = mapped_column(Float)
    retention_potential_score: Mapped[float | None] = mapped_column(Float)
    shareability_score: Mapped[float | None] = mapped_column(Float)
    rights_risk_score: Mapped[float | None] = mapped_column(Float)
    moderation_risk_score: Mapped[float | None] = mapped_column(Float)
    duplicate_risk_score: Mapped[float | None] = mapped_column(Float)
    platform_fit_score: Mapped[float | None] = mapped_column(Float)
    overall_score: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict[str, object] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql")
    )
    scoring_version: Mapped[str] = mapped_column(String(100))
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ranking_confidence_range",
        ),
    )
