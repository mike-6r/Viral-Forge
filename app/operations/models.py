import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base
from app.common.types import UUIDTimestampMixin


class OperationsAlert(UUIDTimestampMixin, Base):
    __tablename__ = "operations_alerts"
    __table_args__ = (UniqueConstraint("brand_id", "dedupe_key", "status", name="uq_operations_alert_dedupe"),)

    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), index=True)
    severity: Mapped[str] = mapped_column(String(20), default="WARNING", index=True)
    category: Mapped[str] = mapped_column(String(80), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(30), default="OPEN", index=True)
    summary: Mapped[str] = mapped_column(Text)
    details_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    occurrences: Mapped[int] = mapped_column(Integer, default=1)


class OperatorTask(UUIDTimestampMixin, Base):
    __tablename__ = "operator_tasks"
    __table_args__ = (UniqueConstraint("brand_id", "dedupe_key", "status", name="uq_operator_task_dedupe"),)

    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), index=True)
    priority: Mapped[str] = mapped_column(String(20), default="NORMAL", index=True)
    task_type: Mapped[str] = mapped_column(String(80), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(30), default="OPEN", index=True)
    title: Mapped[str] = mapped_column(String(255))
    reason: Mapped[str] = mapped_column(Text)
    action_label: Mapped[str] = mapped_column(String(255))
    payload_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
