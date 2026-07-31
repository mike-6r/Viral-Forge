"""Add durable progress and bounded transcript provider metadata.

Revision ID: 0014_real_media_progress
Revises: 0013_clip_opportunities
"""

import sqlalchemy as sa

from alembic import op

revision = "0014_real_media_progress"
down_revision = "0013_clip_opportunities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("video_analyses", sa.Column("current_stage", sa.String(100)))
    op.add_column(
        "video_analyses",
        sa.Column("progress_percent", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "transcript_segments", sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}")
    )


def downgrade() -> None:
    op.drop_column("transcript_segments", "metadata_json")
    op.drop_column("video_analyses", "progress_percent")
    op.drop_column("video_analyses", "current_stage")
