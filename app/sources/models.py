import uuid
from enum import StrEnum

from sqlalchemy import JSON, Boolean, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base
from app.common.types import UUIDTimestampMixin
from app.content.models import Platform


class SourceType(StrEnum):
    MANUAL_URL = "MANUAL_URL"
    MANUAL_UPLOAD = "MANUAL_UPLOAD"
    RSS_FEED = "RSS_FEED"
    ATOM_FEED = "ATOM_FEED"
    PUBLIC_WEBPAGE = "PUBLIC_WEBPAGE"
    OFFICIAL_API = "OFFICIAL_API"
    OWNER_SUBMISSION = "OWNER_SUBMISSION"
    LICENSED_PROVIDER = "LICENSED_PROVIDER"
    PUBLIC_RECORDS_PORTAL = "PUBLIC_RECORDS_PORTAL"
    UNKNOWN = "UNKNOWN"


class SourceStatus(StrEnum):
    PENDING_REVIEW = "PENDING_REVIEW"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    BLOCKED = "BLOCKED"
    REJECTED = "REJECTED"
    ARCHIVED = "ARCHIVED"


class Source(UUIDTimestampMixin, Base):
    __tablename__ = "sources"
    platform: Mapped[Platform] = mapped_column(Enum(Platform))
    external_id: Mapped[str | None] = mapped_column(String(255))
    normalized_url: Mapped[str] = mapped_column(String(2048), unique=True)
    uploader_name: Mapped[str | None] = mapped_column(String(255))
    published_at_source: Mapped[str | None] = mapped_column(String(64))
    provider_metadata: Mapped[dict[str, object] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql")
    )
    source_type: Mapped[SourceType] = mapped_column(Enum(SourceType), default=SourceType.UNKNOWN)
    status: Mapped[SourceStatus] = mapped_column(
        Enum(SourceStatus), default=SourceStatus.PENDING_REVIEW, index=True
    )
    __table_args__ = (
        UniqueConstraint("platform", "external_id", name="uq_source_platform_external"),
    )


class SourcePolicy(UUIDTimestampMixin, Base):
    __tablename__ = "source_policies"
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), index=True
    )
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    policy_version: Mapped[str] = mapped_column(String(100))
    restrictions: Mapped[str | None] = mapped_column(Text)
    allowed_domains: Mapped[list[str] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql")
    )
    blocked_domains: Mapped[list[str] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql")
    )
    permitted_methods: Mapped[list[str] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql")
    )
    permitted_media_types: Mapped[list[str] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql")
    )
    max_file_size_bytes: Mapped[int] = mapped_column(Integer, default=104857600)
    max_feed_items_per_run: Mapped[int] = mapped_column(Integer, default=20)
    feed_recent_window_days: Mapped[int | None] = mapped_column(Integer)
    min_feed_run_interval_seconds: Mapped[int | None] = mapped_column(Integer)
    attribution_required: Mapped[bool] = mapped_column(Boolean, default=True)
    rights_review_required: Mapped[bool] = mapped_column(Boolean, default=True)
    moderation_review_required: Mapped[bool] = mapped_column(Boolean, default=True)
    automatic_import_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    manual_approval_required: Mapped[bool] = mapped_column(Boolean, default=True)
    request_timeout_seconds: Mapped[int] = mapped_column(Integer, default=15)
    redirect_limit: Mapped[int] = mapped_column(Integer, default=3)
    notes: Mapped[str | None] = mapped_column(Text)
