"""Persist original-source candidates, source-quality evidence, and review versioning.

Revision ID: 0010_source_quality_review
Revises: 0009_clipping_mvp
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0010_source_quality_review"
down_revision = "0009_clipping_mvp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    timestamp = "now()" if op.get_bind().dialect.name == "postgresql" else "CURRENT_TIMESTAMP"
    op.create_table(
        "production_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text(timestamp),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text(timestamp),
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("production_projects.id"),
            nullable=False,
        ),
        sa.Column(
            "parent_source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("production_sources.id"),
        ),
        sa.Column("platform", sa.String(50), nullable=False),
        sa.Column("source_url", sa.String(2048), nullable=False),
        sa.Column("resolved_media_url", sa.String(2048)),
        sa.Column("uploader_name", sa.String(500)),
        sa.Column("uploader_account_id", sa.String(255)),
        sa.Column("account_url", sa.String(2048)),
        sa.Column("video_title", sa.String(500)),
        sa.Column("description", sa.Text()),
        sa.Column("upload_date", sa.String(32)),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("frame_rate", sa.Float()),
        sa.Column("bitrate", sa.Integer()),
        sa.Column("file_size_bytes", sa.Integer()),
        sa.Column("view_count", sa.Integer()),
        sa.Column("ownership_classification", sa.String(50), nullable=False),
        sa.Column("official_source_confidence", sa.Float(), nullable=False),
        sa.Column("original_source_confidence", sa.Float(), nullable=False),
        sa.Column("repost_likelihood", sa.Float(), nullable=False),
        sa.Column("watermark_status", sa.String(50), nullable=False),
        sa.Column("watermark_confidence", sa.Float(), nullable=False),
        sa.Column("watermark_regions", sa.JSON(), nullable=False),
        sa.Column("quality_score", sa.Float(), nullable=False),
        sa.Column("quality_components", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("selected_source_reason", sa.Text()),
        sa.Column("quality_status", sa.String(50), nullable=False),
        sa.Column("discovered_at", sa.String(32)),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("fingerprint_json", sa.JSON(), nullable=False),
    )
    for column in (
        "project_id",
        "parent_source_id",
        "platform",
        "ownership_classification",
        "watermark_status",
        "quality_score",
        "quality_status",
    ):
        op.create_index(f"ix_production_sources_{column}", "production_sources", [column])
    op.add_column(
        "production_projects", sa.Column("selected_source_id", postgresql.UUID(as_uuid=True))
    )
    if op.get_bind().dialect.name != "sqlite":
        op.create_foreign_key(
            "fk_projects_selected_source",
            "production_projects",
            "production_sources",
            ["selected_source_id"],
            ["id"],
        )
    op.add_column(
        "production_projects",
        sa.Column("source_decision_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index(
        "ix_production_projects_selected_source_id", "production_projects", ["selected_source_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_production_projects_selected_source_id", table_name="production_projects")
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint("fk_projects_selected_source", "production_projects", type_="foreignkey")
    op.drop_column("production_projects", "source_decision_version")
    op.drop_column("production_projects", "selected_source_id")
    op.drop_table("production_sources")
