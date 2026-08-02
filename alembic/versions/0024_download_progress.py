"""Persist bounded production-source download progress.

Revision ID: 0024_download_progress
Revises: 0023_discord_business_operations
"""

import sqlalchemy as sa

from alembic import op

revision = "0024_download_progress"
down_revision = "0023_discord_business_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable fields preserve all existing projects. New downloads initialize
    # them explicitly when the worker begins.
    op.add_column("production_projects", sa.Column("download_progress_percent", sa.Integer()))
    op.add_column("production_projects", sa.Column("download_progress_stage", sa.String(50)))


def downgrade() -> None:
    op.drop_column("production_projects", "download_progress_stage")
    op.drop_column("production_projects", "download_progress_percent")
