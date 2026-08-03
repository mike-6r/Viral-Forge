import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base
from app.common.types import UUIDTimestampMixin


class ManualPublication(UUIDTimestampMixin, Base):
    __tablename__ = "manual_publications"
    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id"), index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_projects.id"), index=True
    )
    clip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_clips.id"), index=True
    )
    media_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_assets.id"), index=True
    )
    content_package_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_packages.id"), index=True
    )
    content_package_version: Mapped[int] = mapped_column(Integer)
    platform: Mapped[str] = mapped_column(String(50), index=True)
    destination_label: Mapped[str] = mapped_column(String(255))
    public_post_url: Mapped[str] = mapped_column(String(2048))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    recorded_by_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    analytics_eligibility: Mapped[str] = mapped_column(String(50), default="MANUAL_ONLY")
    __table_args__ = (
        UniqueConstraint("platform", "public_post_url", name="uq_manual_publication_platform_url"),
        Index("ix_manual_publications_brand_published", "brand_id", "published_at"),
    )


class ManualAnalyticsCheckpoint(UUIDTimestampMixin, Base):
    __tablename__ = "manual_analytics_checkpoints"
    manual_publication_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("manual_publications.id"), index=True
    )
    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), index=True)
    checkpoint_key: Mapped[str] = mapped_column(String(50))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(30), default="DUE", index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    operator_notes: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("manual_publication_id", "checkpoint_key", name="uq_manual_analytics_checkpoint"),)
