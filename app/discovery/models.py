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

LEGACY_BRAND_ID = uuid.UUID("4e6768ac-d9bc-4eac-8f30-e73ffc510102")


class DiscoveryStatus:
    DISCOVERED = "DISCOVERED"
    NORMALIZED = "NORMALIZED"
    DUPLICATE = "DUPLICATE"
    LOW_RELEVANCE = "LOW_RELEVANCE"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PROCESSING = "PROCESSING"
    PROJECT_CREATED = "PROJECT_CREATED"
    FAILED = "FAILED"
    ARCHIVED = "ARCHIVED"


class DuplicateStatus:
    EXACT = "EXACT"
    PROBABLE = "PROBABLE"
    POSSIBLE = "POSSIBLE"
    NOT_DUPLICATE = "NOT_DUPLICATE"


class DiscoverySource(UUIDTimestampMixin, Base):
    __tablename__ = "discovery_sources"
    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), default=lambda: LEGACY_BRAND_ID, index=True)
    name: Mapped[str] = mapped_column(String(500))
    provider: Mapped[str] = mapped_column(String(50), index=True)
    source_type: Mapped[str] = mapped_column(String(50))
    platform: Mapped[str] = mapped_column(String(50))
    agency_reference: Mapped[str | None] = mapped_column(String(500))
    account_identifier: Mapped[str | None] = mapped_column(String(500))
    public_url: Mapped[str] = mapped_column(String(2048), unique=True)
    country: Mapped[str | None] = mapped_column(String(100))
    state_region: Mapped[str | None] = mapped_column(String(255))
    jurisdiction: Mapped[str | None] = mapped_column(String(500))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    trusted: Mapped[bool] = mapped_column(Boolean, default=False)
    polling_interval_seconds: Mapped[int] = mapped_column(Integer, default=3600)
    last_attempted_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_successful_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error_category: Mapped[str | None] = mapped_column(String(100))
    configuration_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)


class DiscoveredMedia(UUIDTimestampMixin, Base):
    __tablename__ = "discovered_media"
    discovery_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("discovery_sources.id"), index=True
    )
    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), default=lambda: LEGACY_BRAND_ID, index=True)
    provider_item_id: Mapped[str] = mapped_column(String(500))
    canonical_url: Mapped[str] = mapped_column(String(2048), index=True)
    submitted_url: Mapped[str] = mapped_column(String(2048))
    platform: Mapped[str] = mapped_column(String(50))
    title: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    uploader: Mapped[str | None] = mapped_column(String(500))
    uploader_id: Mapped[str | None] = mapped_column(String(255))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    thumbnail_url: Mapped[str | None] = mapped_column(String(2048))
    view_count: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(50))
    location_hints: Mapped[list[str]] = mapped_column(JSON, default=list)
    agency_hints: Mapped[list[str]] = mapped_column(JSON, default=list)
    incident_hints: Mapped[list[str]] = mapped_column(JSON, default=list)
    discovery_score: Mapped[float] = mapped_column(Float, default=0)
    quality_score: Mapped[float | None] = mapped_column(Float)
    source_confidence: Mapped[float | None] = mapped_column(Float)
    watermark_status: Mapped[str] = mapped_column(String(50), default="UNKNOWN")
    duplicate_status: Mapped[str] = mapped_column(String(50), default="NOT_DUPLICATE", index=True)
    lifecycle_status: Mapped[str] = mapped_column(String(50), default="DISCOVERED", index=True)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    production_project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_projects.id"), index=True
    )
    review_version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (
        UniqueConstraint(
            "discovery_source_id", "provider_item_id", name="uq_discovered_media_provider_item"
        ),
    )


class DiscoveryRun(UUIDTimestampMixin, Base):
    __tablename__ = "discovery_runs"
    provider: Mapped[str] = mapped_column(String(50), index=True)
    discovery_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("discovery_sources.id"), index=True
    )
    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), default=lambda: LEGACY_BRAND_ID, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(50), index=True)
    fetched_count: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text)
    cursor: Mapped[str | None] = mapped_column(String(500))
    metrics_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
