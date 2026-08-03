"""Add secure full-quality downloads and operator-recorded manual publishing.

Revision ID: 0033_manual_publish_mobile_download
Revises: 0032_autopilot_policy_scheduling
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0033_manual_publish_mobile_download"
down_revision = "0032_autopilot_policy_scheduling"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column[object]]:
    default = "now()" if op.get_bind().dialect.name == "postgresql" else "CURRENT_TIMESTAMP"
    return [sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text(default)), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text(default))]


def upgrade() -> None:
    op.create_table(
        "manual_publications", *_timestamps(),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_projects.id"), nullable=False),
        sa.Column("clip_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_clips.id"), nullable=False),
        sa.Column("media_asset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("media_assets.id"), nullable=False),
        sa.Column("content_package_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("content_packages.id"), nullable=False),
        sa.Column("content_package_version", sa.Integer(), nullable=False), sa.Column("platform", sa.String(50), nullable=False),
        sa.Column("destination_label", sa.String(255), nullable=False), sa.Column("public_post_url", sa.String(2048), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False), sa.Column("recorded_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("notes", sa.Text()), sa.Column("analytics_eligibility", sa.String(50), nullable=False, server_default="MANUAL_ONLY"),
        sa.UniqueConstraint("platform", "public_post_url", name="uq_manual_publication_platform_url"),
    )
    op.create_index("ix_manual_publications_brand_id", "manual_publications", ["brand_id"])
    op.create_index("ix_manual_publications_project_id", "manual_publications", ["project_id"])
    op.create_index("ix_manual_publications_clip_id", "manual_publications", ["clip_id"])
    op.create_index("ix_manual_publications_media_asset_id", "manual_publications", ["media_asset_id"])
    op.create_index("ix_manual_publications_content_package_id", "manual_publications", ["content_package_id"])
    op.create_index("ix_manual_publications_recorded_by_id", "manual_publications", ["recorded_by_id"])
    op.create_index("ix_manual_publications_published_at", "manual_publications", ["published_at"])
    op.create_index("ix_manual_publications_platform", "manual_publications", ["platform"])
    op.create_index("ix_manual_publications_brand_published", "manual_publications", ["brand_id", "published_at"])
    op.create_table(
        "manual_analytics_checkpoints", *_timestamps(),
        sa.Column("manual_publication_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("manual_publications.id"), nullable=False),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id"), nullable=False),
        sa.Column("checkpoint_key", sa.String(50), nullable=False), sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="DUE"), sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("snoozed_until", sa.DateTime(timezone=True)), sa.Column("operator_notes", sa.Text()),
        sa.UniqueConstraint("manual_publication_id", "checkpoint_key", name="uq_manual_analytics_checkpoint"),
    )
    for column in ["manual_publication_id", "brand_id", "due_at", "status", "snoozed_until"]:
        op.create_index(f"ix_manual_analytics_checkpoints_{column}", "manual_analytics_checkpoints", [column])
    op.create_table(
        "clip_download_grants", *_timestamps(),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_projects.id"), nullable=False),
        sa.Column("clip_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_clips.id"), nullable=False),
        sa.Column("media_asset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("media_assets.id"), nullable=False),
        sa.Column("content_package_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("content_packages.id")),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True), sa.Column("created_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False), sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("access_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("maximum_access_count", sa.Integer()),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True)), sa.Column("version_id", sa.Integer(), nullable=False, server_default="1"),
    )
    for column in ["brand_id", "project_id", "clip_id", "media_asset_id", "content_package_id", "created_by_id", "expires_at", "revoked_at"]:
        op.create_index(f"ix_clip_download_grants_{column}", "clip_download_grants", [column])
    op.create_index("ix_clip_download_grants_active", "clip_download_grants", ["clip_id", "revoked_at", "expires_at"])
    with op.batch_alter_table("post_analytics_snapshots") as batch:
        batch.alter_column(
            "publish_request_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True
        )
        batch.add_column(sa.Column("manual_publication_id", postgresql.UUID(as_uuid=True)))
        batch.create_foreign_key(
            "fk_post_analytics_manual_publication",
            "manual_publications",
            ["manual_publication_id"],
            ["id"],
        )
        batch.create_index("ix_post_analytics_snapshots_manual_publication_id", ["manual_publication_id"])


def downgrade() -> None:
    with op.batch_alter_table("post_analytics_snapshots") as batch:
        batch.drop_index("ix_post_analytics_snapshots_manual_publication_id")
        batch.drop_constraint("fk_post_analytics_manual_publication", type_="foreignkey")
        batch.drop_column("manual_publication_id")
        batch.alter_column("publish_request_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)
    op.drop_index("ix_clip_download_grants_active", table_name="clip_download_grants")
    for column in ["brand_id", "project_id", "clip_id", "media_asset_id", "content_package_id", "created_by_id", "expires_at", "revoked_at"]:
        op.drop_index(f"ix_clip_download_grants_{column}", table_name="clip_download_grants")
    op.drop_table("clip_download_grants")
    for column in ["manual_publication_id", "brand_id", "due_at", "status", "snoozed_until"]:
        op.drop_index(f"ix_manual_analytics_checkpoints_{column}", table_name="manual_analytics_checkpoints")
    op.drop_table("manual_analytics_checkpoints")
    op.drop_index("ix_manual_publications_brand_published", table_name="manual_publications")
    for column in ["brand_id", "project_id", "clip_id", "media_asset_id", "content_package_id", "recorded_by_id", "published_at", "platform"]:
        op.drop_index(f"ix_manual_publications_{column}", table_name="manual_publications")
    op.drop_table("manual_publications")
