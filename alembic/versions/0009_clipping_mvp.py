"""Add the minimal project, clip, and posting queue records for the clipping MVP.

Revision ID: 0009_clipping_mvp
Revises: 0008_feed_optimistic_locking
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0009_clipping_mvp"
down_revision = "0008_feed_optimistic_locking"
branch_labels = None
depends_on = None


def _common() -> list[sa.Column[object]]:
    timestamp = "now()" if op.get_bind().dialect.name == "postgresql" else "CURRENT_TIMESTAMP"
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "production_projects",
        sa.Column("source_platform", sa.String(50), nullable=False),
        sa.Column("source_url", sa.String(2048), nullable=False, unique=True),
        sa.Column("source_video_id", sa.String(255)),
        sa.Column("source_title", sa.String(500)),
        sa.Column("source_channel", sa.String(500)),
        sa.Column("source_duration_seconds", sa.Float()),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("source_storage_key", sa.String(1024)),
        sa.Column("discord_guild_id", sa.String(50)),
        sa.Column("discord_channel_id", sa.String(50)),
        sa.Column("discord_message_id", sa.String(50)),
        sa.Column(
            "created_actor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text()),
        *_common(),
    )
    op.create_table(
        "production_clips",
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("production_projects.id"),
            nullable=False,
        ),
        sa.Column("clip_number", sa.Integer(), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("storage_key", sa.String(1024)),
        sa.Column("render_status", sa.String(50), nullable=False),
        sa.Column("approval_status", sa.String(50), nullable=False),
        sa.Column("caption", sa.Text()),
        sa.Column("discord_message_id", sa.String(50)),
        sa.Column("publication_status", sa.String(50), nullable=False),
        *_common(),
        sa.UniqueConstraint("project_id", "clip_number", name="uq_production_clip_number"),
    )
    op.create_table(
        "posting_queue_items",
        sa.Column(
            "clip_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("production_clips.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("target_platform", sa.String(50), nullable=False),
        sa.Column("target_account_id", sa.String(255)),
        sa.Column("caption", sa.Text()),
        sa.Column("scheduled_for", sa.String(64)),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("published_platform_id", sa.String(255)),
        sa.Column("published_url", sa.String(2048)),
        *_common(),
    )
    for table, column in (
        ("production_projects", "status"),
        ("production_projects", "source_video_id"),
        ("production_clips", "project_id"),
        ("production_clips", "render_status"),
        ("production_clips", "approval_status"),
        ("production_clips", "publication_status"),
        ("posting_queue_items", "status"),
    ):
        op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    for table in ("posting_queue_items", "production_clips", "production_projects"):
        op.drop_table(table)
