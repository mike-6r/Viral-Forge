"""Explicit, review-gated publishing records.  These records never contain credentials."""

import uuid

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base
from app.common.types import UUIDTimestampMixin


class PublishRequestStatus:
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    SCHEDULED = "SCHEDULED"
    QUEUED = "QUEUED"
    INITIALIZING = "INITIALIZING"
    TRANSFERRING = "TRANSFERRING"
    PROCESSING = "PROCESSING"
    DRAFT_READY = "DRAFT_READY"
    OPERATOR_COMPLETION_REQUIRED = "OPERATOR_COMPLETION_REQUIRED"
    UNKNOWN_REMOTE_OUTCOME = "UNKNOWN_REMOTE_OUTCOME"
    MANUAL_RECONCILIATION_REQUIRED = "MANUAL_RECONCILIATION_REQUIRED"
    UPLOADING = "UPLOADING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class PublishingAccountConnection(UUIDTimestampMixin, Base):
    __tablename__ = "publishing_account_connections"
    __table_args__ = (UniqueConstraint("destination_account_id", name="uq_publishing_connection_destination"),)

    destination_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("destination_accounts.id"), index=True)
    connection_state: Mapped[str] = mapped_column(String(50), default="NOT_CONNECTED", index=True)
    provider_account_id: Mapped[str | None] = mapped_column(String(255))
    provider_channel_url: Mapped[str | None] = mapped_column(String(2048))
    checked_at: Mapped[str | None] = mapped_column(String(64))
    last_error_category: Mapped[str | None] = mapped_column(String(100))
    last_error_summary: Mapped[str | None] = mapped_column(Text)


class PublishReviewGate(UUIDTimestampMixin, Base):
    __tablename__ = "publish_review_gates"
    __table_args__ = (UniqueConstraint("clip_id", name="uq_publish_review_gate_clip"),)

    clip_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("production_clips.id"), index=True)
    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), index=True)
    rights_required: Mapped[bool] = mapped_column(Boolean, default=False)
    rights_disposition: Mapped[str] = mapped_column(String(50), default="NOT_APPLICABLE")
    moderation_disposition: Mapped[str] = mapped_column(String(50), default="PENDING")
    rights_reviewer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    moderation_reviewer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    notes: Mapped[str | None] = mapped_column(Text)


class PublishRequest(UUIDTimestampMixin, Base):
    __tablename__ = "publish_requests"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_publish_request_idempotency"),)

    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), index=True)
    queue_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("posting_queue_items.id"), index=True)
    clip_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("production_clips.id"), index=True)
    content_package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("content_packages.id"), index=True)
    destination_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("destination_accounts.id"), index=True)
    requested_by_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    confirmed_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    decision_type: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(50), default=PublishRequestStatus.AWAITING_CONFIRMATION, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255))
    scheduled_for: Mapped[str | None] = mapped_column(String(64), index=True)
    confirmed_at: Mapped[str | None] = mapped_column(String(64))
    platform_metadata: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    upload_progress_percent: Mapped[int] = mapped_column(Integer, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[str | None] = mapped_column(String(64), index=True)
    failure_category: Mapped[str | None] = mapped_column(String(100))
    failure_summary: Mapped[str | None] = mapped_column(Text)
    remote_post_id: Mapped[str | None] = mapped_column(String(255))
    remote_post_url: Mapped[str | None] = mapped_column(String(2048))
    cancelled_before_upload: Mapped[bool] = mapped_column(Boolean, default=False)
    # These fields are provider-neutral extensions.  They deliberately contain
    # identifiers and state only; OAuth credentials and upload URLs never enter
    # this table.
    provider_mode: Mapped[str | None] = mapped_column(String(50))
    provider_remote_status: Mapped[str | None] = mapped_column(String(100))
    provider_upload_session_id: Mapped[str | None] = mapped_column(String(255))
    provider_settings: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    content_package_generation_version: Mapped[int | None] = mapped_column(Integer)
    transfer_started_at: Mapped[str | None] = mapped_column(String(64))
    operator_completion_state: Mapped[str | None] = mapped_column(String(50))
    reconciliation_reason: Mapped[str | None] = mapped_column(Text)


class TikTokOAuthState(UUIDTimestampMixin, Base):
    """One-time OAuth state.  Only a digest is stored, never the raw value."""

    __tablename__ = "tiktok_oauth_states"
    __table_args__ = (UniqueConstraint("state_digest", name="uq_tiktok_oauth_state_digest"),)

    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), index=True)
    destination_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("destination_accounts.id"), index=True)
    state_digest: Mapped[str] = mapped_column(String(128))
    requested_scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    expires_at: Mapped[str] = mapped_column(String(64), index=True)
    consumed_at: Mapped[str | None] = mapped_column(String(64))


class TikTokCreatorCapability(UUIDTimestampMixin, Base):
    """Safe creator capabilities captured from TikTok immediately before transfer."""

    __tablename__ = "tiktok_creator_capabilities"
    __table_args__ = (UniqueConstraint("destination_account_id", name="uq_tiktok_capability_destination"),)

    destination_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("destination_accounts.id"), index=True)
    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), index=True)
    creator_identity_reference: Mapped[str] = mapped_column(String(255))
    creator_username: Mapped[str | None] = mapped_column(String(255))
    creator_nickname: Mapped[str | None] = mapped_column(String(500))
    privacy_options: Mapped[list[str]] = mapped_column(JSON, default=list)
    max_video_duration_seconds: Mapped[int | None] = mapped_column(Integer)
    comments_disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    duet_disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    stitch_disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    provider_log_id: Mapped[str | None] = mapped_column(String(255))
    captured_at: Mapped[str] = mapped_column(String(64), index=True)


class PublishAttempt(UUIDTimestampMixin, Base):
    __tablename__ = "publish_attempts"
    __table_args__ = (UniqueConstraint("publish_request_id", "attempt_number", name="uq_publish_attempt_number"),)

    publish_request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("publish_requests.id"), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(50), index=True)
    failure_category: Mapped[str | None] = mapped_column(String(100))
    detail: Mapped[str | None] = mapped_column(Text)
    remote_post_id: Mapped[str | None] = mapped_column(String(255))
    remote_post_url: Mapped[str | None] = mapped_column(String(2048))
