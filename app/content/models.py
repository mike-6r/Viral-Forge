import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base
from app.common.types import UUIDTimestampMixin


class ContentStatus(StrEnum):
    DISCOVERED = "DISCOVERED"
    IMPORTED = "IMPORTED"
    SOURCE_VERIFICATION_REQUIRED = "SOURCE_VERIFICATION_REQUIRED"
    RIGHTS_REVIEW_REQUIRED = "RIGHTS_REVIEW_REQUIRED"
    MODERATION_REQUIRED = "MODERATION_REQUIRED"
    READY_FOR_RANKING = "READY_FOR_RANKING"
    RANKED = "RANKED"
    PROCESSING_QUEUED = "PROCESSING_QUEUED"
    PROCESSING = "PROCESSING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SCHEDULED = "SCHEDULED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    PUBLISH_FAILED = "PUBLISH_FAILED"
    ARCHIVED = "ARCHIVED"
    BLOCKED = "BLOCKED"


class Platform(StrEnum):
    MANUAL = "MANUAL"
    TIKTOK = "TIKTOK"
    YOUTUBE = "YOUTUBE"
    INSTAGRAM = "INSTAGRAM"
    FACEBOOK = "FACEBOOK"
    OTHER = "OTHER"


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    STALE = "STALE"


class ContentItem(UUIDTimestampMixin, Base):
    __tablename__ = "content_items"
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ContentStatus] = mapped_column(
        Enum(ContentStatus), default=ContentStatus.DISCOVERED, index=True
    )
    source_provenance_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    external_reference: Mapped[str | None] = mapped_column(String(255), index=True)
    version_id: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    __table_args__ = (Index("ix_content_items_created_at", "created_at"),)
    __mapper_args__ = {"version_id_col": version_id}


class ContentSource(UUIDTimestampMixin, Base):
    __tablename__ = "content_sources"
    content_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_items.id"), index=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), index=True
    )
    is_original: Mapped[bool] = mapped_column(Boolean, default=True)
    source_url: Mapped[str] = mapped_column(String(2048))
    __table_args__ = (UniqueConstraint("content_id", "source_id", name="uq_content_source"),)


class MediaAsset(UUIDTimestampMixin, Base):
    __tablename__ = "media_assets"
    # Upload assets pre-date production projects.  It is intentionally optional so
    # the same durable inventory can describe production media without inventing a
    # ContentItem merely to satisfy a foreign key.
    content_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_items.id"), index=True
    )
    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("brands.id"),
        default=lambda: uuid.UUID("4e6768ac-d9bc-4eac-8f30-e73ffc510102"),
        index=True,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_projects.id"), index=True
    )
    clip_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_clips.id"), index=True
    )
    storage_key: Mapped[str] = mapped_column(String(1024), unique=True)
    media_type: Mapped[str] = mapped_column(String(100))
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    checksum: Mapped[str | None] = mapped_column(String(128))
    storage_provider: Mapped[str] = mapped_column(String(50), default="local")
    original_filename: Mapped[str | None] = mapped_column(String(255))
    display_filename: Mapped[str | None] = mapped_column(String(255))
    detected_media_type: Mapped[str | None] = mapped_column(String(100))
    declared_media_type: Mapped[str | None] = mapped_column(String(100))
    container_type: Mapped[str | None] = mapped_column(String(50))
    file_size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    uploader_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), index=True
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), index=True
    )
    asset_status: Mapped[str] = mapped_column(
        String(50), default="VERIFICATION_REQUIRED", index=True
    )
    correlation_id: Mapped[str | None] = mapped_column(String(100), index=True)
    storage_metadata: Mapped[dict[str, object] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql")
    )
    asset_type: Mapped[str] = mapped_column(String(50), default="UPLOAD_ASSET", index=True)
    content_type: Mapped[str | None] = mapped_column(String(100))
    lifecycle_state: Mapped[str] = mapped_column(String(50), default="ACTIVE", index=True)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retention_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    administrative_hold: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deletion_reason: Mapped[str | None] = mapped_column(Text)
    former_size_bytes: Mapped[int | None] = mapped_column(Integer)
    deletion_attempts: Mapped[int] = mapped_column(Integer, default=0)
    deletion_error: Mapped[str | None] = mapped_column(Text)
    version_id: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    __table_args__ = (
        Index("uq_media_assets_checksum", "checksum", unique=True),
        Index("ix_media_assets_clip_type", "clip_id", "asset_type"),
    )
    __mapper_args__ = {"version_id_col": version_id}


class ProcessingJob(UUIDTimestampMixin, Base):
    __tablename__ = "processing_jobs"
    content_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_items.id"), index=True
    )
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.QUEUED, index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    error_category: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("progress >= 0 AND progress <= 100", name="processing_progress_range"),
        CheckConstraint(
            "attempts >= 0 AND max_attempts > 0 AND attempts <= max_attempts",
            name="processing_attempt_range",
        ),
    )


class ClipCandidate(UUIDTimestampMixin, Base):
    __tablename__ = "clip_candidates"
    content_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_items.id"), index=True
    )
    start_seconds: Mapped[float] = mapped_column(Float)
    end_seconds: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(50), default="PROPOSED")


class PublishingJob(UUIDTimestampMixin, Base):
    __tablename__ = "publishing_jobs"
    content_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_items.id"), index=True
    )
    platform_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("platform_accounts.id")
    )
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.QUEUED, index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_category: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("progress >= 0 AND progress <= 100", name="publishing_progress_range"),
        CheckConstraint(
            "attempts >= 0 AND max_attempts > 0 AND attempts <= max_attempts",
            name="publishing_attempt_range",
        ),
    )


class PublishedPost(UUIDTimestampMixin, Base):
    __tablename__ = "published_posts"
    content_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_items.id"), index=True
    )
    platform: Mapped[Platform] = mapped_column(Enum(Platform))
    platform_post_id: Mapped[str] = mapped_column(String(255))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("platform", "platform_post_id", name="uq_platform_post"),)
