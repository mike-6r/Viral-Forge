import uuid

from sqlalchemy import JSON, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

# Audit events are written by worker and service entry points that may not load
# the API module, so register the brand FK target with this model.
from app.brands.models import Brand  # noqa: F401
from app.common.db import Base
from app.common.types import UUIDTimestampMixin


class AuditEvent(UUIDTimestampMixin, Base):
    __tablename__ = "audit_events"
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), index=True
    )
    entity_type: Mapped[str] = mapped_column(String(100), index=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("brands.id"),
        default=lambda: uuid.UUID("4e6768ac-d9bc-4eac-8f30-e73ffc510102"),
        index=True,
    )
    event_name: Mapped[str] = mapped_column(String(150))
    reason: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(String(100), index=True)
    payload: Mapped[dict[str, object] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql")
    )
    __table_args__ = (Index("ix_audit_events_created_at", "created_at"),)
