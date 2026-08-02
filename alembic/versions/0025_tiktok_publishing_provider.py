"""Add safe TikTok publishing, OAuth-state, and capability persistence.

Revision ID: 0025_tiktok_publishing_provider
Revises: 0024_download_progress
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0025_tiktok_publishing_provider"
down_revision = "0024_download_progress"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column[object]]:
    default = "now()" if op.get_bind().dialect.name == "postgresql" else "CURRENT_TIMESTAMP"
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text(default)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text(default)),
    ]


def upgrade() -> None:
    with op.batch_alter_table("publish_requests") as batch:
        batch.add_column(sa.Column("provider_mode", sa.String(50)))
        batch.add_column(sa.Column("provider_remote_status", sa.String(100)))
        batch.add_column(sa.Column("provider_upload_session_id", sa.String(255)))
        batch.add_column(sa.Column("provider_settings", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        batch.add_column(sa.Column("content_package_generation_version", sa.Integer()))
        batch.add_column(sa.Column("transfer_started_at", sa.String(64)))
        batch.add_column(sa.Column("operator_completion_state", sa.String(50)))
        batch.add_column(sa.Column("reconciliation_reason", sa.Text()))
    op.create_table(
        "tiktok_oauth_states",
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id"), nullable=False),
        sa.Column("destination_account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("destination_accounts.id"), nullable=False),
        sa.Column("state_digest", sa.String(128), nullable=False),
        sa.Column("requested_scopes", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.String(64), nullable=False),
        sa.Column("consumed_at", sa.String(64)),
        *_timestamps(),
        sa.UniqueConstraint("state_digest", name="uq_tiktok_oauth_state_digest"),
    )
    op.create_index("ix_tiktok_oauth_states_brand_id", "tiktok_oauth_states", ["brand_id"])
    op.create_index("ix_tiktok_oauth_states_destination_account_id", "tiktok_oauth_states", ["destination_account_id"])
    op.create_index("ix_tiktok_oauth_states_expires_at", "tiktok_oauth_states", ["expires_at"])
    op.create_table(
        "tiktok_creator_capabilities",
        sa.Column("destination_account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("destination_accounts.id"), nullable=False),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id"), nullable=False),
        sa.Column("creator_identity_reference", sa.String(255), nullable=False),
        sa.Column("creator_username", sa.String(255)),
        sa.Column("creator_nickname", sa.String(500)),
        sa.Column("privacy_options", sa.JSON(), nullable=False),
        sa.Column("max_video_duration_seconds", sa.Integer()),
        sa.Column("comments_disabled", sa.Boolean(), nullable=False),
        sa.Column("duet_disabled", sa.Boolean(), nullable=False),
        sa.Column("stitch_disabled", sa.Boolean(), nullable=False),
        sa.Column("provider_log_id", sa.String(255)),
        sa.Column("captured_at", sa.String(64), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("destination_account_id", name="uq_tiktok_capability_destination"),
    )
    op.create_index("ix_tiktok_creator_capabilities_destination_account_id", "tiktok_creator_capabilities", ["destination_account_id"])
    op.create_index("ix_tiktok_creator_capabilities_brand_id", "tiktok_creator_capabilities", ["brand_id"])
    op.create_index("ix_tiktok_creator_capabilities_captured_at", "tiktok_creator_capabilities", ["captured_at"])


def downgrade() -> None:
    op.drop_table("tiktok_creator_capabilities")
    op.drop_table("tiktok_oauth_states")
    with op.batch_alter_table("publish_requests") as batch:
        batch.drop_column("reconciliation_reason")
        batch.drop_column("operator_completion_state")
        batch.drop_column("transfer_started_at")
        batch.drop_column("content_package_generation_version")
        batch.drop_column("provider_settings")
        batch.drop_column("provider_upload_session_id")
        batch.drop_column("provider_remote_status")
        batch.drop_column("provider_mode")
