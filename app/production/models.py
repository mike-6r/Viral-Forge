import uuid

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base
from app.common.types import UUIDTimestampMixin

LEGACY_BRAND_ID = uuid.UUID("4e6768ac-d9bc-4eac-8f30-e73ffc510102")


class ProductionProject(UUIDTimestampMixin, Base):
    __tablename__ = "production_projects"
    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id"), default=lambda: LEGACY_BRAND_ID, index=True
    )
    source_platform: Mapped[str] = mapped_column(String(50), default="YOUTUBE")
    source_url: Mapped[str] = mapped_column(String(2_048), unique=True)
    source_video_id: Mapped[str | None] = mapped_column(String(255), index=True)
    source_title: Mapped[str | None] = mapped_column(String(500))
    source_channel: Mapped[str | None] = mapped_column(String(500))
    source_duration_seconds: Mapped[float | None] = mapped_column(Float)
    selected_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("production_sources.id", use_alter=True, name="fk_projects_selected_source"),
        index=True,
    )
    source_decision_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(50), default="CREATED", index=True)
    source_storage_key: Mapped[str | None] = mapped_column(String(1_024))
    discord_guild_id: Mapped[str | None] = mapped_column(String(50))
    discord_channel_id: Mapped[str | None] = mapped_column(String(50))
    discord_message_id: Mapped[str | None] = mapped_column(String(50))
    created_actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), index=True
    )
    last_error: Mapped[str | None] = mapped_column(Text)


class ProductionSource(UUIDTimestampMixin, Base):
    """Submitted and discovered public source records; credentials are never stored."""

    __tablename__ = "production_sources"
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_projects.id"), index=True
    )
    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), default=lambda: LEGACY_BRAND_ID, index=True)
    parent_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_sources.id"), index=True
    )
    platform: Mapped[str] = mapped_column(String(50), default="YOUTUBE", index=True)
    source_url: Mapped[str] = mapped_column(String(2_048))
    resolved_media_url: Mapped[str | None] = mapped_column(String(2_048))
    uploader_name: Mapped[str | None] = mapped_column(String(500))
    uploader_account_id: Mapped[str | None] = mapped_column(String(255))
    account_url: Mapped[str | None] = mapped_column(String(2_048))
    video_title: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    upload_date: Mapped[str | None] = mapped_column(String(32))
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    frame_rate: Mapped[float | None] = mapped_column(Float)
    bitrate: Mapped[int | None] = mapped_column(Integer)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer)
    view_count: Mapped[int | None] = mapped_column(Integer)
    ownership_classification: Mapped[str] = mapped_column(String(50), default="UNKNOWN", index=True)
    official_source_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    original_source_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    repost_likelihood: Mapped[float] = mapped_column(Float, default=0.0)
    watermark_status: Mapped[str] = mapped_column(String(50), default="UNKNOWN", index=True)
    watermark_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    watermark_regions: Mapped[list[dict[str, float]]] = mapped_column(JSON, default=list)
    quality_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    quality_components: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    selected_source_reason: Mapped[str | None] = mapped_column(Text)
    quality_status: Mapped[str] = mapped_column(String(50), default="ACCEPTABLE", index=True)
    discovered_at: Mapped[str | None] = mapped_column(String(32))
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    fingerprint_json: Mapped[dict[str, str | float | int]] = mapped_column(JSON, default=dict)


class ProductionClip(UUIDTimestampMixin, Base):
    __tablename__ = "production_clips"
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_projects.id"), index=True
    )
    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), default=lambda: LEGACY_BRAND_ID, index=True)
    clip_number: Mapped[int] = mapped_column(Integer)
    start_seconds: Mapped[float] = mapped_column(Float)
    end_seconds: Mapped[float] = mapped_column(Float)
    duration_seconds: Mapped[float] = mapped_column(Float)
    storage_key: Mapped[str | None] = mapped_column(String(1_024))
    render_status: Mapped[str] = mapped_column(String(50), default="PENDING", index=True)
    approval_status: Mapped[str] = mapped_column(String(50), default="PENDING", index=True)
    caption: Mapped[str | None] = mapped_column(Text)
    discord_message_id: Mapped[str | None] = mapped_column(String(50))
    publication_status: Mapped[str] = mapped_column(String(50), default="NOT_QUEUED", index=True)
    __table_args__ = (
        UniqueConstraint("project_id", "clip_number", name="uq_production_clip_number"),
    )


class PostingQueueItem(UUIDTimestampMixin, Base):
    __tablename__ = "posting_queue_items"
    clip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_clips.id"), unique=True
    )
    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), default=lambda: LEGACY_BRAND_ID, index=True)
    target_platform: Mapped[str] = mapped_column(String(50), default="YOUTUBE")
    target_account_id: Mapped[str | None] = mapped_column(String(255))
    caption: Mapped[str | None] = mapped_column(Text)
    scheduled_for: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(50), default="READY_TO_POST", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    published_platform_id: Mapped[str | None] = mapped_column(String(255))
    published_url: Mapped[str | None] = mapped_column(String(2_048))
