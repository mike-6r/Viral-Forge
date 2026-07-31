"""Create review-gated, explicit publishing records.

Revision ID: 0017_publishing_foundation
Revises: 0016_multi_brand_foundation
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0017_publishing_foundation"
down_revision = "0016_multi_brand_foundation"
branch_labels = None
depends_on = None


def _common() -> list[sa.Column[object]]:
    timestamp = "now()" if op.get_bind().dialect.name == "postgresql" else "CURRENT_TIMESTAMP"
    return [sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text(timestamp)), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text(timestamp))]


def upgrade() -> None:
    op.create_table("publishing_account_connections", sa.Column("destination_account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("destination_accounts.id"), nullable=False), sa.Column("connection_state", sa.String(50), nullable=False), sa.Column("provider_account_id", sa.String(255)), sa.Column("provider_channel_url", sa.String(2048)), sa.Column("checked_at", sa.String(64)), sa.Column("last_error_category", sa.String(100)), sa.Column("last_error_summary", sa.Text()), *_common(), sa.UniqueConstraint("destination_account_id", name="uq_publishing_connection_destination"))
    op.create_index("ix_publishing_account_connections_destination_account_id", "publishing_account_connections", ["destination_account_id"])
    op.create_index("ix_publishing_account_connections_connection_state", "publishing_account_connections", ["connection_state"])
    op.create_table("publish_review_gates", sa.Column("clip_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_clips.id"), nullable=False), sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id"), nullable=False), sa.Column("rights_required", sa.Boolean(), nullable=False), sa.Column("rights_disposition", sa.String(50), nullable=False), sa.Column("moderation_disposition", sa.String(50), nullable=False), sa.Column("rights_reviewer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")), sa.Column("moderation_reviewer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")), sa.Column("notes", sa.Text()), *_common(), sa.UniqueConstraint("clip_id", name="uq_publish_review_gate_clip"))
    op.create_index("ix_publish_review_gates_clip_id", "publish_review_gates", ["clip_id"])
    op.create_index("ix_publish_review_gates_brand_id", "publish_review_gates", ["brand_id"])
    op.create_table("publish_requests", sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id"), nullable=False), sa.Column("queue_item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("posting_queue_items.id"), nullable=False), sa.Column("clip_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_clips.id"), nullable=False), sa.Column("content_package_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("content_packages.id"), nullable=False), sa.Column("destination_account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("destination_accounts.id"), nullable=False), sa.Column("requested_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False), sa.Column("confirmed_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")), sa.Column("decision_type", sa.String(50), nullable=False), sa.Column("status", sa.String(50), nullable=False), sa.Column("idempotency_key", sa.String(255), nullable=False), sa.Column("scheduled_for", sa.String(64)), sa.Column("confirmed_at", sa.String(64)), sa.Column("platform_metadata", sa.JSON(), nullable=False), sa.Column("upload_progress_percent", sa.Integer(), nullable=False), sa.Column("attempt_count", sa.Integer(), nullable=False), sa.Column("next_attempt_at", sa.String(64)), sa.Column("failure_category", sa.String(100)), sa.Column("failure_summary", sa.Text()), sa.Column("remote_post_id", sa.String(255)), sa.Column("remote_post_url", sa.String(2048)), sa.Column("cancelled_before_upload", sa.Boolean(), nullable=False), *_common(), sa.UniqueConstraint("idempotency_key", name="uq_publish_request_idempotency"))
    for name, column in (("ix_publish_requests_brand_id", "brand_id"), ("ix_publish_requests_queue_item_id", "queue_item_id"), ("ix_publish_requests_clip_id", "clip_id"), ("ix_publish_requests_content_package_id", "content_package_id"), ("ix_publish_requests_destination_account_id", "destination_account_id"), ("ix_publish_requests_requested_by_id", "requested_by_id"), ("ix_publish_requests_status", "status"), ("ix_publish_requests_scheduled_for", "scheduled_for"), ("ix_publish_requests_next_attempt_at", "next_attempt_at")):
        op.create_index(name, "publish_requests", [column])
    op.create_table("publish_attempts", sa.Column("publish_request_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("publish_requests.id"), nullable=False), sa.Column("attempt_number", sa.Integer(), nullable=False), sa.Column("status", sa.String(50), nullable=False), sa.Column("failure_category", sa.String(100)), sa.Column("detail", sa.Text()), sa.Column("remote_post_id", sa.String(255)), sa.Column("remote_post_url", sa.String(2048)), *_common(), sa.UniqueConstraint("publish_request_id", "attempt_number", name="uq_publish_attempt_number"))
    op.create_index("ix_publish_attempts_publish_request_id", "publish_attempts", ["publish_request_id"])
    op.create_index("ix_publish_attempts_status", "publish_attempts", ["status"])


def downgrade() -> None:
    op.drop_table("publish_attempts")
    op.drop_table("publish_requests")
    op.drop_table("publish_review_gates")
    op.drop_table("publishing_account_connections")
