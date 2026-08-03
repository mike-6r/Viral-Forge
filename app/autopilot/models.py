"""Durable, brand-scoped autopilot state with no provider credentials."""

import uuid

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base
from app.common.types import UUIDTimestampMixin


class AutopilotPolicy(UUIDTimestampMixin, Base):
    __tablename__ = "autopilot_policies"
    __table_args__ = (UniqueConstraint("brand_id", name="uq_autopilot_policy_brand"),)

    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id"), index=True
    )
    automation_level: Mapped[str] = mapped_column(String(32), default="MANUAL", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_paused: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    config_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )


class AutopilotGlobalControl(UUIDTimestampMixin, Base):
    """One durable global safety switch; each brand retains its own pause."""

    __tablename__ = "autopilot_global_controls"

    control_key: Mapped[str] = mapped_column(String(50), unique=True, default="GLOBAL")
    version: Mapped[int] = mapped_column(Integer, default=1)
    emergency_stop: Mapped[bool] = mapped_column(Boolean, default=False)
    discovery_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    processing_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    publishing_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )


class AutopilotDecision(UUIDTimestampMixin, Base):
    __tablename__ = "autopilot_decisions"

    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id"), index=True
    )
    policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("autopilot_policies.id"), index=True
    )
    policy_version: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(64), index=True)
    decision: Mapped[str] = mapped_column(String(32), index=True)
    object_type: Mapped[str] = mapped_column(String(100), index=True)
    object_id: Mapped[str] = mapped_column(String(64), index=True)
    reason_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    explanation: Mapped[str] = mapped_column(Text)
    evidence_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    thresholds_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    actuals_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    missing_evidence: Mapped[list[str]] = mapped_column(JSON, default=list)
    confidence: Mapped[int | None] = mapped_column(Integer)
    correlation_key: Mapped[str | None] = mapped_column(String(255), index=True)


class AutopilotRun(UUIDTimestampMixin, Base):
    """One active, idempotent stage per object; workers update this rather than race."""

    __tablename__ = "autopilot_runs"
    __table_args__ = (
        UniqueConstraint(
            "brand_id", "object_type", "object_id", "stage", name="uq_autopilot_run_stage"
        ),
    )

    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id"), index=True
    )
    object_type: Mapped[str] = mapped_column(String(100))
    object_id: Mapped[str] = mapped_column(String(64))
    stage: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    heartbeat_at: Mapped[str | None] = mapped_column(String(64), index=True)
    recovery_class: Mapped[str | None] = mapped_column(String(32))
    last_error: Mapped[str | None] = mapped_column(Text)
    decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("autopilot_decisions.id")
    )


class AutopilotScheduleSlot(UUIDTimestampMixin, Base):
    __tablename__ = "autopilot_schedule_slots"
    __table_args__ = (
        UniqueConstraint(
            "destination_account_id", "scheduled_for", name="uq_autopilot_destination_slot"
        ),
    )

    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id"), index=True
    )
    destination_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("destination_accounts.id"), index=True
    )
    queue_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("posting_queue_items.id"), unique=True
    )
    clip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_clips.id"), index=True
    )
    content_package_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_packages.id"), index=True
    )
    content_package_generation_version: Mapped[int] = mapped_column(Integer)
    policy_version: Mapped[int] = mapped_column(Integer)
    scheduled_for: Mapped[str] = mapped_column(String(64), index=True)
    provider_mode: Mapped[str] = mapped_column(String(50), default="HUMAN_CONFIRMATION")
    privacy: Mapped[str] = mapped_column(String(50), default="private")
    status: Mapped[str] = mapped_column(String(32), default="RESERVED", index=True)
    confirmation_required: Mapped[bool] = mapped_column(Boolean, default=True)
    hold_reason: Mapped[str | None] = mapped_column(Text)


class AutopilotQueueRank(UUIDTimestampMixin, Base):
    __tablename__ = "autopilot_queue_ranks"
    __table_args__ = (UniqueConstraint("queue_item_id", name="uq_autopilot_queue_rank_item"),)

    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id"), index=True
    )
    queue_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("posting_queue_items.id"), index=True
    )
    rank_score: Mapped[int] = mapped_column(Integer, index=True)
    rank_position: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", index=True)
    explanation: Mapped[str] = mapped_column(Text)
    evidence_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    manual_override: Mapped[str | None] = mapped_column(String(32))


class AutopilotException(UUIDTimestampMixin, Base):
    __tablename__ = "autopilot_exceptions"
    __table_args__ = (
        UniqueConstraint("brand_id", "dedupe_key", "status", name="uq_autopilot_exception_dedupe"),
    )

    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id"), index=True
    )
    decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("autopilot_decisions.id"), index=True
    )
    category: Mapped[str] = mapped_column(String(80), index=True)
    severity: Mapped[str] = mapped_column(String(20), default="WARNING", index=True)
    dedupe_key: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="OPEN", index=True)
    object_type: Mapped[str] = mapped_column(String(100))
    object_id: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    recommended_action: Mapped[str] = mapped_column(String(255))
    retry_state: Mapped[str] = mapped_column(String(32), default="OPERATOR_REQUIRED")
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
