"""Add explainable, human-reviewed clip opportunities.

Revision ID: 0013_clip_opportunities
Revises: 0012_ai_analysis_foundation
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0013_clip_opportunities"
down_revision = "0012_ai_analysis_foundation"
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
        "opportunity_generation_runs",
        sa.Column("analysis_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("video_analyses.id"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_projects.id"), nullable=False),
        sa.Column("generation_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("provider_name", sa.String(100), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("opportunity_count", sa.Integer(), nullable=False),
        sa.Column("error_summary", sa.Text()),
        *_common(),
        sa.UniqueConstraint("analysis_id", "generation_version", name="uq_opportunity_run_analysis_version"),
    )
    op.create_table(
        "clip_opportunities",
        sa.Column("analysis_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("video_analyses.id"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_projects.id"), nullable=False),
        sa.Column("generation_version", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.Float(), nullable=False),
        sa.Column("end_time", sa.Float(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("overall_score", sa.Float(), nullable=False),
        sa.Column("review_status", sa.String(50), nullable=False),
        sa.Column("generation_status", sa.String(50), nullable=False),
        sa.Column("review_version", sa.Integer(), nullable=False),
        sa.Column("overlap_percentage", sa.Float(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("generated_clip_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_clips.id"), unique=True),
        sa.Column("generation_error", sa.Text()),
        *_common(),
        sa.UniqueConstraint("analysis_id", "generation_version", "start_time", "end_time", name="uq_opportunity_analysis_window"),
    )
    op.create_table(
        "opportunity_reasons",
        sa.Column("opportunity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clip_opportunities.id"), nullable=False),
        sa.Column("reason_type", sa.String(100), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        *_common(),
    )
    op.create_table(
        "clip_opportunity_versions",
        sa.Column("opportunity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clip_opportunities.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("review_status", sa.String(50), nullable=False),
        sa.Column("generation_status", sa.String(50), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("decision_reason", sa.Text()),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        *_common(),
        sa.UniqueConstraint("opportunity_id", "version", name="uq_opportunity_version"),
    )
    for table, column in (
        ("opportunity_generation_runs", "analysis_id"),
        ("opportunity_generation_runs", "project_id"),
        ("opportunity_generation_runs", "status"),
        ("clip_opportunities", "analysis_id"),
        ("clip_opportunities", "project_id"),
        ("clip_opportunities", "generation_version"),
        ("clip_opportunities", "overall_score"),
        ("clip_opportunities", "review_status"),
        ("clip_opportunities", "generation_status"),
        ("opportunity_reasons", "opportunity_id"),
        ("opportunity_reasons", "reason_type"),
        ("clip_opportunity_versions", "opportunity_id"),
    ):
        op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    for table in ("clip_opportunity_versions", "opportunity_reasons", "clip_opportunities", "opportunity_generation_runs"):
        op.drop_table(table)
