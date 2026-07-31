"""Add reusable technical analysis records without changing clipping.

Revision ID: 0012_ai_analysis_foundation
Revises: 0011_discovery_engine
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0012_ai_analysis_foundation"
down_revision = "0011_discovery_engine"
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
        "video_analyses",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_projects.id"), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_sources.id")),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("fps", sa.Float()),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("frame_count", sa.Integer()),
        sa.Column("transcript_language", sa.String(50)),
        sa.Column("analysis_version", sa.String(100), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        *_common(),
        sa.UniqueConstraint("project_id", "analysis_version", name="uq_video_analysis_project_version"),
    )
    op.create_table(
        "analysis_segments",
        sa.Column("analysis_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("video_analyses.id"), nullable=False),
        sa.Column("start_time", sa.Float(), nullable=False),
        sa.Column("end_time", sa.Float(), nullable=False),
        sa.Column("segment_type", sa.String(100), nullable=False),
        sa.Column("confidence", sa.Float()),
        sa.Column("score", sa.Float()),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        *_common(),
    )
    op.create_table(
        "transcript_segments",
        sa.Column("analysis_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("video_analyses.id"), nullable=False),
        sa.Column("start_time", sa.Float(), nullable=False),
        sa.Column("end_time", sa.Float(), nullable=False),
        sa.Column("speaker", sa.String(255)),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float()),
        *_common(),
    )
    op.create_table(
        "analysis_events",
        sa.Column("analysis_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("video_analyses.id"), nullable=False),
        sa.Column("timestamp", sa.Float(), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("confidence", sa.Float()),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        *_common(),
    )
    for table, column in (
        ("video_analyses", "project_id"),
        ("video_analyses", "source_id"),
        ("video_analyses", "status"),
        ("analysis_segments", "analysis_id"),
        ("analysis_segments", "segment_type"),
        ("transcript_segments", "analysis_id"),
        ("analysis_events", "analysis_id"),
        ("analysis_events", "event_type"),
    ):
        op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    for table in ("analysis_events", "transcript_segments", "analysis_segments", "video_analyses"):
        op.drop_table(table)
