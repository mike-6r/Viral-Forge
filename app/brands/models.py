"""Tenant configuration records. Credential material is deliberately not persisted here."""

import uuid

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base
from app.common.types import UUIDTimestampMixin


class Workspace(UUIDTimestampMixin, Base):
    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    timezone: Mapped[str] = mapped_column(String(100), default="UTC")
    is_legacy: Mapped[bool] = mapped_column(Boolean, default=False)


class Brand(UUIDTimestampMixin, Base):
    __tablename__ = "brands"
    __table_args__ = (UniqueConstraint("workspace_id", "slug", name="uq_brand_workspace_slug"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_legacy: Mapped[bool] = mapped_column(Boolean, default=False)


class BrandMembership(UUIDTimestampMixin, Base):
    __tablename__ = "brand_memberships"
    __table_args__ = (UniqueConstraint("brand_id", "user_id", name="uq_brand_member"),)

    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(50), default="VIEWER")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class ContentProfile(UUIDTimestampMixin, Base):
    __tablename__ = "content_profiles"
    __table_args__ = (UniqueConstraint("brand_id", name="uq_content_profile_brand"),)

    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), index=True)
    niche_name: Mapped[str] = mapped_column(String(255))
    discovery_categories: Mapped[list[str]] = mapped_column(JSON, default=list)
    included_keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    excluded_keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    preferred_source_providers: Mapped[list[str]] = mapped_column(JSON, default=list)
    min_clip_duration_seconds: Mapped[int] = mapped_column(Integer, default=15)
    max_clip_duration_seconds: Mapped[int] = mapped_column(Integer, default=60)
    opportunity_weights_json: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    opportunity_profile_reference: Mapped[str | None] = mapped_column(String(255))
    caption_tone: Mapped[str] = mapped_column(String(255), default="neutral")
    title_style: Mapped[str] = mapped_column(String(255), default="factual")
    hashtag_rules: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    branding_behavior: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    review_requirements: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    maximum_posts_per_day: Mapped[int] = mapped_column(Integer, default=0)
    target_platforms: Mapped[list[str]] = mapped_column(JSON, default=list)
    language: Mapped[str] = mapped_column(String(50), default="und")
    timezone: Mapped[str] = mapped_column(String(100), default="UTC")


class SourceAccount(UUIDTimestampMixin, Base):
    __tablename__ = "source_accounts"
    __table_args__ = (UniqueConstraint("brand_id", "provider", "account_reference", name="uq_source_account_brand_provider"),)

    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), index=True)
    provider: Mapped[str] = mapped_column(String(50))
    account_reference: Mapped[str] = mapped_column(String(500))
    public_url: Mapped[str | None] = mapped_column(String(2048))
    display_name: Mapped[str | None] = mapped_column(String(500))
    provider_metadata: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class DestinationAccount(UUIDTimestampMixin, Base):
    __tablename__ = "destination_accounts"
    __table_args__ = (UniqueConstraint("brand_id", "provider", "account_reference", name="uq_destination_account_brand_provider"),)

    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), index=True)
    provider: Mapped[str] = mapped_column(String(50))
    account_reference: Mapped[str] = mapped_column(String(500))
    credential_reference_id: Mapped[str | None] = mapped_column(String(500))
    display_name: Mapped[str | None] = mapped_column(String(500))
    provider_metadata: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class BrandingProfile(UUIDTimestampMixin, Base):
    __tablename__ = "branding_profiles"
    __table_args__ = (UniqueConstraint("brand_id", name="uq_branding_profile_brand"),)

    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(255))
    attribution_template: Mapped[str | None] = mapped_column(Text)
    behavior_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)


class ReviewPolicy(UUIDTimestampMixin, Base):
    __tablename__ = "review_policies"
    __table_args__ = (UniqueConstraint("brand_id", name="uq_review_policy_brand"),)

    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), index=True)
    requirements_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    required_review_count: Mapped[int] = mapped_column(Integer, default=1)


class PostingPolicy(UUIDTimestampMixin, Base):
    __tablename__ = "posting_policies"
    __table_args__ = (UniqueConstraint("brand_id", name="uq_posting_policy_brand"),)

    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), index=True)
    maximum_posts_per_day: Mapped[int] = mapped_column(Integer, default=0)
    target_platforms: Mapped[list[str]] = mapped_column(JSON, default=list)
    timezone: Mapped[str] = mapped_column(String(100), default="UTC")
    policy_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
