"""Add versioned, human-reviewed content packages for rendered clips.

Revision ID: 0015_content_package
Revises: 0014_real_media_progress
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0015_content_package"
down_revision = "0014_real_media_progress"
branch_labels = None
depends_on = None


def _common() -> list[sa.Column[object]]:
    timestamp = "now()" if op.get_bind().dialect.name == "postgresql" else "CURRENT_TIMESTAMP"
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text(timestamp)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text(timestamp)),
    ]


def upgrade() -> None:
    op.create_table(
        "content_packages",
        sa.Column("clip_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_clips.id"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_projects.id"), nullable=False),
        sa.Column("generation_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("review_version", sa.Integer(), nullable=False),
        sa.Column("provider_name", sa.String(100), nullable=False),
        sa.Column("model_name", sa.String(255)),
        sa.Column("provider_version", sa.String(255)),
        sa.Column("language", sa.String(50), nullable=False),
        sa.Column("content_category", sa.String(100), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("fields_json", sa.JSON(), nullable=False),
        sa.Column("verified_facts_json", sa.JSON(), nullable=False),
        sa.Column("transcript_statements_json", sa.JSON(), nullable=False),
        sa.Column("uncertainty_json", sa.JSON(), nullable=False),
        sa.Column("warnings_json", sa.JSON(), nullable=False),
        *_common(),
        sa.UniqueConstraint("clip_id", "generation_version", name="uq_content_package_clip_version"),
    )
    op.create_table(
        "content_package_versions",
        sa.Column("content_package_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("content_packages.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        *_common(),
        sa.UniqueConstraint("content_package_id", "version", name="uq_content_package_review_version"),
    )
    for table, column in (("content_packages", "clip_id"), ("content_packages", "project_id"), ("content_packages", "status"), ("content_package_versions", "content_package_id")):
        op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    op.drop_table("content_package_versions")
    op.drop_table("content_packages")
