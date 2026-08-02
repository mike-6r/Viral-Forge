"""Persisted correction plans.  These records never contain shell commands or paths."""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base
from app.common.types import UUIDTimestampMixin


class CorrectionPlanStatus:
    DRAFT = "DRAFT"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    APPROVED = "APPROVED"
    QUEUED = "QUEUED"
    RENDERING = "RENDERING"
    RENDERED = "RENDERED"
    REINSPECTING = "REINSPECTING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    STALE = "STALE"


class ClipCorrectionPlan(UUIDTimestampMixin, Base):
    __tablename__ = "clip_correction_plans"
    __table_args__ = (UniqueConstraint("source_clip_id", "plan_version", name="uq_clip_correction_plan_version"),)

    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("production_projects.id"), index=True)
    source_clip_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("production_clips.id"), index=True)
    source_media_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("media_assets.id"), index=True)
    source_inspection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("rendered_media_inspections.id"), index=True)
    plan_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default=CorrectionPlanStatus.DRAFT, index=True)
    expected_review_version: Mapped[int] = mapped_column(Integer, default=1)
    created_by_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    rejected_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rendering_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rendering_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_category: Mapped[str | None] = mapped_column(String(100))
    operator_note: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, default="")
    expected_score_improvement: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    result_clip_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("production_clips.id"), index=True)
    result_media_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("media_assets.id"), index=True)
    result_inspection_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("rendered_media_inspections.id"), index=True)
    renderer_config_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    comparison_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    review_version: Mapped[int] = mapped_column(Integer, default=1)


class ClipCorrectionAction(UUIDTimestampMixin, Base):
    __tablename__ = "clip_correction_actions"
    __table_args__ = (UniqueConstraint("plan_id", "action_order", name="uq_clip_correction_action_order"),)

    plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clip_correction_plans.id"), index=True)
    originating_issue_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("rendered_media_inspection_issues.id"), index=True)
    action_order: Mapped[int] = mapped_column(Integer)
    action_type: Mapped[str] = mapped_column(String(100), index=True)
    start_seconds: Mapped[float | None] = mapped_column(Float)
    end_seconds: Mapped[float | None] = mapped_column(Float)
    current_value: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    proposed_value: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    minimum_value: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    maximum_value: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    operator_selected: Mapped[bool] = mapped_column(Boolean, default=True)
    renderer_parameters: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
