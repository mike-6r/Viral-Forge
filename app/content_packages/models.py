"""Versioned content-package records; they never schedule or publish clips."""

import uuid

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base
from app.common.types import UUIDTimestampMixin

LEGACY_BRAND_ID = uuid.UUID("4e6768ac-d9bc-4eac-8f30-e73ffc510102")


class ContentPackageStatus:
    QUEUED = "QUEUED"
    GENERATING = "GENERATING"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    STALE = "STALE"


class ContentPackage(UUIDTimestampMixin, Base):
    __tablename__ = "content_packages"
    __table_args__ = (
        UniqueConstraint("clip_id", "generation_version", name="uq_content_package_clip_version"),
    )

    clip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_clips.id"), index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_projects.id"), index=True
    )
    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), default=lambda: LEGACY_BRAND_ID, index=True)
    generation_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(50), default=ContentPackageStatus.PENDING, index=True)
    review_version: Mapped[int] = mapped_column(Integer, default=1)
    provider_name: Mapped[str] = mapped_column(String(100))
    model_name: Mapped[str | None] = mapped_column(String(255))
    provider_version: Mapped[str | None] = mapped_column(String(255))
    language: Mapped[str] = mapped_column(String(50), default="und")
    content_category: Mapped[str] = mapped_column(String(100), default="SOURCE_CLIP")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    explanation: Mapped[str] = mapped_column(Text)
    fields_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    verified_facts_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    transcript_statements_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    uncertainty_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    warnings_json: Mapped[list[str]] = mapped_column(JSON, default=list)


class ContentPackageVersion(UUIDTimestampMixin, Base):
    __tablename__ = "content_package_versions"
    __table_args__ = (
        UniqueConstraint("content_package_id", "version", name="uq_content_package_review_version"),
    )

    content_package_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_packages.id"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(50))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str | None] = mapped_column(Text)
    snapshot_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
