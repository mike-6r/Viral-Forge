"""Versioned, evidence-only rendered-media inspection records.

The records in this module must never be used to mutate rendering, approval,
queueing, scheduling, upload, or publishing state.
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base
from app.common.types import UUIDTimestampMixin


class RenderedMediaInspectionStatus:
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    STALE = "STALE"


class RenderedMediaInspectionReviewStatus:
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class RenderedMediaInspection(UUIDTimestampMixin, Base):
    __tablename__ = "rendered_media_inspections"
    __table_args__ = (
        UniqueConstraint("clip_id", "media_asset_id", "inspection_version", name="uq_rendered_media_inspection_version"),
    )

    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("production_projects.id"), index=True)
    clip_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("production_clips.id"), index=True)
    media_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("media_assets.id"), index=True)
    inspection_version: Mapped[int] = mapped_column(Integer, default=1)
    provider: Mapped[str] = mapped_column(String(100), default="local_ffmpeg")
    provider_version: Mapped[str] = mapped_column(String(100), default="v1")
    safe_area_profile: Mapped[str] = mapped_column(String(100), default="generic_9_16")
    status: Mapped[str] = mapped_column(String(30), default=RenderedMediaInspectionStatus.QUEUED, index=True)
    current_stage: Mapped[str] = mapped_column(String(80), default="QUEUED")
    progress_percent: Mapped[float] = mapped_column(Float, default=0.0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    technical_score: Mapped[float | None] = mapped_column(Float)
    visual_score: Mapped[float | None] = mapped_column(Float)
    subtitle_score: Mapped[float | None] = mapped_column(Float)
    audio_score: Mapped[float | None] = mapped_column(Float)
    framing_score: Mapped[float | None] = mapped_column(Float)
    safe_area_score: Mapped[float | None] = mapped_column(Float)
    hook_score: Mapped[float | None] = mapped_column(Float)
    overall_score: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    warnings_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    summary: Mapped[str] = mapped_column(Text, default="")
    evidence_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    failure_category: Mapped[str | None] = mapped_column(String(100))
    review_status: Mapped[str] = mapped_column(String(30), default=RenderedMediaInspectionReviewStatus.PENDING, index=True)
    review_version: Mapped[int] = mapped_column(Integer, default=1)
    operator_note: Mapped[str | None] = mapped_column(Text)
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    decision_reason: Mapped[str | None] = mapped_column(Text)


class RenderedMediaInspectionIssue(UUIDTimestampMixin, Base):
    __tablename__ = "rendered_media_inspection_issues"

    inspection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("rendered_media_inspections.id"), index=True)
    issue_type: Mapped[str] = mapped_column(String(100), index=True)
    severity: Mapped[str] = mapped_column(String(20), index=True)
    start_seconds: Mapped[float | None] = mapped_column(Float)
    end_seconds: Mapped[float | None] = mapped_column(Float)
    frame_index: Mapped[int | None] = mapped_column(Integer)
    evidence_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    measured_value_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    expected_range_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    explanation: Mapped[str] = mapped_column(Text)
    recommendation: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
