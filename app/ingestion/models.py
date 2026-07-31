import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base
from app.common.types import UUIDTimestampMixin


class IngestionMethod(StrEnum):
    MANUAL_URL = "MANUAL_URL"
    MANUAL_UPLOAD = "MANUAL_UPLOAD"
    RSS_FEED = "RSS_FEED"
    ATOM_FEED = "ATOM_FEED"


class IngestionStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    CANCELLED = "CANCELLED"
    STALE = "STALE"


class DuplicateOutcome(StrEnum):
    NEW = "NEW"
    EXACT_URL_DUPLICATE = "EXACT_URL_DUPLICATE"
    CANONICAL_URL_DUPLICATE = "CANONICAL_URL_DUPLICATE"
    EXTERNAL_ID_DUPLICATE = "EXTERNAL_ID_DUPLICATE"
    FILE_HASH_DUPLICATE = "FILE_HASH_DUPLICATE"
    FEED_GUID_DUPLICATE = "FEED_GUID_DUPLICATE"
    POSSIBLE_DUPLICATE = "POSSIBLE_DUPLICATE"


class IngestionJob(UUIDTimestampMixin, Base):
    __tablename__ = "ingestion_jobs"
    method: Mapped[IngestionMethod] = mapped_column(Enum(IngestionMethod), index=True)
    status: Mapped[IngestionStatus] = mapped_column(
        Enum(IngestionStatus), default=IngestionStatus.QUEUED, index=True
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), index=True
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), index=True
    )
    requested_url: Mapped[str | None] = mapped_column(String(2048))
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    error_category: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    result_content_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_items.id")
    )
    result_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_assets.id")
    )
    correlation_id: Mapped[str | None] = mapped_column(String(100), index=True)
    result_metadata: Mapped[dict[str, object] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FeedSubscription(UUIDTimestampMixin, Base):
    __tablename__ = "feed_subscriptions"
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), unique=True
    )
    feed_url: Mapped[str] = mapped_column(String(2048), unique=True)
    feed_type: Mapped[IngestionMethod] = mapped_column(Enum(IngestionMethod))
    polling_interval_seconds: Mapped[int] = mapped_column(Integer, default=3600)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    etag: Mapped[str | None] = mapped_column(String(255))
    last_modified: Mapped[str | None] = mapped_column(String(255))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(50), default="PENDING_VALIDATION", index=True)
    final_url: Mapped[str | None] = mapped_column(String(2048))
    title: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(50))
    site_url: Mapped[str | None] = mapped_column(String(2048))
    last_error_category: Mapped[str | None] = mapped_column(String(100))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    recent_item_window_days: Mapped[int] = mapped_column(Integer, default=30)
    max_items_per_run: Mapped[int] = mapped_column(Integer, default=20)
    active_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    active_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    correlation_id: Mapped[str | None] = mapped_column(String(100), index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), unique=True)
    version_id: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class FeedEntry(UUIDTimestampMixin, Base):
    __tablename__ = "feed_entries"
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("feed_subscriptions.id"), index=True
    )
    entry_guid: Mapped[str] = mapped_column(String(1024))
    content_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_items.id")
    )
    link: Mapped[str | None] = mapped_column(String(2048))
    raw_metadata: Mapped[dict[str, object] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql")
    )
    identity_strategy: Mapped[str] = mapped_column(String(50), default="GUID")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    title: Mapped[str | None] = mapped_column(String(500))
    author: Mapped[str | None] = mapped_column(String(500))
    updated_at_source: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    import_outcome: Mapped[str] = mapped_column(String(100), default="IMPORTED")
    failure_category: Mapped[str | None] = mapped_column(String(100))
    __table_args__ = (UniqueConstraint("subscription_id", "entry_guid", name="uq_feed_entry_guid"),)


class SourceVerification(UUIDTimestampMixin, Base):
    __tablename__ = "source_verifications"
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), index=True
    )
    verifier_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    outcome: Mapped[str] = mapped_column(String(50))
    evidence_reference: Mapped[str | None] = mapped_column(String(2048))
    notes: Mapped[str | None] = mapped_column(Text)


class DuplicateMatch(UUIDTimestampMixin, Base):
    __tablename__ = "duplicate_matches"
    content_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_items.id"), index=True
    )
    matched_content_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_items.id")
    )
    outcome: Mapped[DuplicateOutcome] = mapped_column(Enum(DuplicateOutcome))
    evidence: Mapped[str] = mapped_column(String(2048))
