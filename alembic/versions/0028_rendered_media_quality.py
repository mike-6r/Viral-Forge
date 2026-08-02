"""Add advisory rendered-media inspection records.

Revision ID: 0028_rendered_media_quality
Revises: 0027_ai_producer_recommendations
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0028_rendered_media_quality"
down_revision = "0027_ai_producer_recommendations"
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
    with op.batch_alter_table("content_profiles") as batch:
        batch.add_column(sa.Column("rendered_media_inspection_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.create_table(
        "rendered_media_inspections", *_timestamps(),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_projects.id"), nullable=False),
        sa.Column("clip_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_clips.id"), nullable=False),
        sa.Column("media_asset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("media_assets.id")),
        sa.Column("inspection_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("provider", sa.String(100), nullable=False, server_default="local_ffmpeg"),
        sa.Column("provider_version", sa.String(100), nullable=False, server_default="local-v1"),
        sa.Column("safe_area_profile", sa.String(100), nullable=False, server_default="generic_9_16"),
        sa.Column("status", sa.String(30), nullable=False, server_default="QUEUED"),
        sa.Column("current_stage", sa.String(80), nullable=False, server_default="QUEUED"),
        sa.Column("progress_percent", sa.Float(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("completed_at", sa.DateTime(timezone=True)), sa.Column("failed_at", sa.DateTime(timezone=True)),
        sa.Column("technical_score", sa.Float()), sa.Column("visual_score", sa.Float()), sa.Column("subtitle_score", sa.Float()),
        sa.Column("audio_score", sa.Float()), sa.Column("framing_score", sa.Float()), sa.Column("safe_area_score", sa.Float()), sa.Column("hook_score", sa.Float()), sa.Column("overall_score", sa.Float()),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("warnings_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("evidence_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("failure_category", sa.String(100)),
        sa.Column("review_status", sa.String(30), nullable=False, server_default="PENDING"),
        sa.Column("review_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("operator_note", sa.Text()), sa.Column("decided_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")), sa.Column("decision_reason", sa.Text()),
        sa.UniqueConstraint("clip_id", "media_asset_id", "inspection_version", name="uq_rendered_media_inspection_version"),
    )
    for name, column in [
        ("ix_rendered_media_inspections_brand_id", "brand_id"), ("ix_rendered_media_inspections_project_id", "project_id"),
        ("ix_rendered_media_inspections_clip_id", "clip_id"), ("ix_rendered_media_inspections_media_asset_id", "media_asset_id"),
        ("ix_rendered_media_inspections_status", "status"), ("ix_rendered_media_inspections_review_status", "review_status"),
        ("ix_rendered_media_inspections_decided_by_id", "decided_by_id"),
    ]:
        op.create_index(name, "rendered_media_inspections", [column])
    op.create_table(
        "rendered_media_inspection_issues", *_timestamps(),
        sa.Column("inspection_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("rendered_media_inspections.id"), nullable=False),
        sa.Column("issue_type", sa.String(100), nullable=False), sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("start_seconds", sa.Float()), sa.Column("end_seconds", sa.Float()), sa.Column("frame_index", sa.Integer()),
        sa.Column("evidence_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("measured_value_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("expected_range_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("explanation", sa.Text(), nullable=False), sa.Column("recommendation", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
    )
    for name, column in [
        ("ix_rendered_media_inspection_issues_inspection_id", "inspection_id"),
        ("ix_rendered_media_inspection_issues_issue_type", "issue_type"),
        ("ix_rendered_media_inspection_issues_severity", "severity"),
    ]:
        op.create_index(name, "rendered_media_inspection_issues", [column])


def downgrade() -> None:
    for name in ["ix_rendered_media_inspection_issues_severity", "ix_rendered_media_inspection_issues_issue_type", "ix_rendered_media_inspection_issues_inspection_id"]:
        op.drop_index(name, table_name="rendered_media_inspection_issues")
    op.drop_table("rendered_media_inspection_issues")
    for name in ["ix_rendered_media_inspections_decided_by_id", "ix_rendered_media_inspections_review_status", "ix_rendered_media_inspections_status", "ix_rendered_media_inspections_media_asset_id", "ix_rendered_media_inspections_clip_id", "ix_rendered_media_inspections_project_id", "ix_rendered_media_inspections_brand_id"]:
        op.drop_index(name, table_name="rendered_media_inspections")
    op.drop_table("rendered_media_inspections")
    with op.batch_alter_table("content_profiles") as batch:
        batch.drop_column("rendered_media_inspection_json")
