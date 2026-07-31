"""Read-only performance and operator-feedback records for published requests."""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base
from app.common.types import UUIDTimestampMixin


class PerformanceSnapshot(UUIDTimestampMixin, Base):
    """Legacy generic analytics record retained for schema compatibility."""

    __tablename__ = "performance_snapshots"
    published_post_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("published_posts.id"), index=True
    )
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    views: Mapped[int] = mapped_column(Integer, default=0)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    comments: Mapped[int] = mapped_column(Integer, default=0)
    shares: Mapped[int] = mapped_column(Integer, default=0)


class PostAnalyticsSnapshot(UUIDTimestampMixin, Base):
    __tablename__ = "post_analytics_snapshots"
    __table_args__ = (UniqueConstraint("publish_request_id", "captured_at", name="uq_post_analytics_snapshot_time"),)

    publish_request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("publish_requests.id"), index=True)
    clip_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("production_clips.id"), index=True)
    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), index=True)
    provider: Mapped[str] = mapped_column(String(50), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    collection_source: Mapped[str] = mapped_column(String(50))
    views: Mapped[int | None] = mapped_column(Integer)
    watch_time_seconds: Mapped[float | None] = mapped_column(Float)
    average_view_duration_seconds: Mapped[float | None] = mapped_column(Float)
    retention_percentage: Mapped[float | None] = mapped_column(Float)
    likes: Mapped[int | None] = mapped_column(Integer)
    comments: Mapped[int | None] = mapped_column(Integer)
    shares: Mapped[int | None] = mapped_column(Integer)
    saves: Mapped[int | None] = mapped_column(Integer)
    followers_gained: Mapped[int | None] = mapped_column(Integer)
    clicks: Mapped[int | None] = mapped_column(Integer)
    platform_revenue: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(10))
    raw_metadata: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)


class OperatorFeedbackLabel(UUIDTimestampMixin, Base):
    __tablename__ = "operator_feedback_labels"

    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), index=True)
    publish_request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("publish_requests.id"), index=True)
    clip_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("production_clips.id"), index=True)
    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    label: Mapped[str] = mapped_column(String(100), index=True)
    value: Mapped[str] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)


class AnalyticsRefreshRun(UUIDTimestampMixin, Base):
    __tablename__ = "analytics_refresh_runs"

    brand_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), index=True)
    provider: Mapped[str] = mapped_column(String(50), index=True)
    status: Mapped[str] = mapped_column(String(50), index=True)
    processed_count: Mapped[int] = mapped_column(Integer, default=0)
    snapshot_count: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text)
