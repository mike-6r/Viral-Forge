"""Persistence for detected opportunities and their explainable decisions."""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base
from app.common.types import UUIDTimestampMixin

LEGACY_BRAND_ID = uuid.UUID("4e6768ac-d9bc-4eac-8f30-e73ffc510102")


class OpportunityReviewStatus:
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    STALE = "STALE"


class OpportunityGenerationStatus:
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class OpportunityRunStatus:
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class OpportunityGenerationRun(UUIDTimestampMixin, Base):
    __tablename__ = "opportunity_generation_runs"
    __table_args__ = (
        UniqueConstraint("analysis_id", "generation_version", name="uq_opportunity_run_analysis_version"),
    )

    analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_analyses.id"), index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_projects.id"), index=True
    )
    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), default=lambda: LEGACY_BRAND_ID, index=True)
    generation_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(50), default=OpportunityRunStatus.QUEUED, index=True)
    provider_name: Mapped[str] = mapped_column(String(100), default="rule")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opportunity_count: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text)


class ClipOpportunity(UUIDTimestampMixin, Base):
    __tablename__ = "clip_opportunities"
    __table_args__ = (
        UniqueConstraint(
            "analysis_id",
            "generation_version",
            "start_time",
            "end_time",
            name="uq_opportunity_analysis_window",
        ),
    )

    analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_analyses.id"), index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_projects.id"), index=True
    )
    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), default=lambda: LEGACY_BRAND_ID, index=True)
    generation_version: Mapped[int] = mapped_column(Integer, default=1, index=True)
    start_time: Mapped[float] = mapped_column(Float)
    end_time: Mapped[float] = mapped_column(Float)
    duration_seconds: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    overall_score: Mapped[float] = mapped_column(Float, index=True)
    review_status: Mapped[str] = mapped_column(
        String(50), default=OpportunityReviewStatus.PENDING, index=True
    )
    generation_status: Mapped[str] = mapped_column(
        String(50), default=OpportunityGenerationStatus.PENDING, index=True
    )
    review_version: Mapped[int] = mapped_column(Integer, default=1)
    overlap_percentage: Mapped[float] = mapped_column(Float, default=0.0)
    explanation: Mapped[str] = mapped_column(Text)
    generated_clip_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_clips.id"), unique=True
    )
    generation_error: Mapped[str | None] = mapped_column(Text)


class OpportunityReason(UUIDTimestampMixin, Base):
    __tablename__ = "opportunity_reasons"

    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clip_opportunities.id"), index=True
    )
    reason_type: Mapped[str] = mapped_column(String(100), index=True)
    score: Mapped[float] = mapped_column(Float)
    weight: Mapped[float] = mapped_column(Float)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)


class ClipOpportunityVersion(UUIDTimestampMixin, Base):
    __tablename__ = "clip_opportunity_versions"
    __table_args__ = (
        UniqueConstraint("opportunity_id", "version", name="uq_opportunity_version"),
    )

    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clip_opportunities.id"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    review_status: Mapped[str] = mapped_column(String(50))
    generation_status: Mapped[str] = mapped_column(String(50))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    snapshot_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
