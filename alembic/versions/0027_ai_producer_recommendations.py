"""Add approval-first AI Producer recommendation and quality-report records.

Revision ID: 0027_ai_producer_recommendations
Revises: 0026_tiktok_credential_lifecycle
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0027_ai_producer_recommendations"
down_revision = "0026_tiktok_credential_lifecycle"
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
    op.create_table(
        "producer_recommendations",
        *_timestamps(),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id"), nullable=False),
        sa.Column("discovered_media_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("discovered_media.id")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_projects.id")),
        sa.Column("clip_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_clips.id")),
        sa.Column("content_package_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("content_packages.id")),
        sa.Column("recommendation_type", sa.String(80), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="PENDING"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reasoning", sa.Text(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("recommendation_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("operator_edit_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("prediction_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("provider_name", sa.String(100), nullable=False, server_default="local_producer"),
        sa.Column("model_name", sa.String(255)),
        sa.Column("provider_version", sa.String(255)),
        sa.Column("review_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("decided_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("decision_reason", sa.Text()),
    )
    for name, column in [
        ("ix_producer_recommendations_brand_id", "brand_id"), ("ix_producer_recommendations_discovered_media_id", "discovered_media_id"),
        ("ix_producer_recommendations_project_id", "project_id"), ("ix_producer_recommendations_clip_id", "clip_id"),
        ("ix_producer_recommendations_content_package_id", "content_package_id"), ("ix_producer_recommendations_recommendation_type", "recommendation_type"),
        ("ix_producer_recommendations_status", "status"), ("ix_producer_recommendations_decided_by_id", "decided_by_id"),
    ]:
        op.create_index(name, "producer_recommendations", [column])
    op.create_table(
        "clip_quality_reports", *_timestamps(),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_projects.id"), nullable=False),
        sa.Column("clip_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_clips.id"), nullable=False),
        sa.Column("report_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("hook_quality", sa.Float(), nullable=False), sa.Column("pacing_quality", sa.Float(), nullable=False),
        sa.Column("context_quality", sa.Float(), nullable=False), sa.Column("retention_estimate", sa.Float(), nullable=False),
        sa.Column("subtitle_quality", sa.Float(), nullable=False), sa.Column("title_quality", sa.Float(), nullable=False),
        sa.Column("caption_quality", sa.Float(), nullable=False), sa.Column("hashtag_quality", sa.Float(), nullable=False),
        sa.Column("overall_readiness", sa.Float(), nullable=False), sa.Column("reasoning", sa.Text(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("recommendations_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("prediction_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("provider_name", sa.String(100), nullable=False, server_default="local_producer"),
        sa.Column("model_name", sa.String(255)), sa.Column("provider_version", sa.String(255)),
        sa.UniqueConstraint("clip_id", "report_version", name="uq_clip_quality_report_version"),
    )
    for name, column in [("ix_clip_quality_reports_brand_id", "brand_id"), ("ix_clip_quality_reports_project_id", "project_id"), ("ix_clip_quality_reports_clip_id", "clip_id")]:
        op.create_index(name, "clip_quality_reports", [column])
    op.create_table(
        "producer_outcome_evaluations", *_timestamps(),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id"), nullable=False),
        sa.Column("recommendation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("producer_recommendations.id"), nullable=False),
        sa.Column("clip_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_clips.id")),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("post_analytics_snapshots.id"), nullable=False),
        sa.Column("predicted_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("observed_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("evaluation_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.UniqueConstraint("recommendation_id", "snapshot_id", name="uq_producer_outcome_recommendation_snapshot"),
    )
    for name, column in [("ix_producer_outcome_evaluations_brand_id", "brand_id"), ("ix_producer_outcome_evaluations_recommendation_id", "recommendation_id"), ("ix_producer_outcome_evaluations_clip_id", "clip_id"), ("ix_producer_outcome_evaluations_snapshot_id", "snapshot_id")]:
        op.create_index(name, "producer_outcome_evaluations", [column])


def downgrade() -> None:
    for table, names in [
        ("producer_outcome_evaluations", ["ix_producer_outcome_evaluations_snapshot_id", "ix_producer_outcome_evaluations_clip_id", "ix_producer_outcome_evaluations_recommendation_id", "ix_producer_outcome_evaluations_brand_id"]),
        ("clip_quality_reports", ["ix_clip_quality_reports_clip_id", "ix_clip_quality_reports_project_id", "ix_clip_quality_reports_brand_id"]),
        ("producer_recommendations", ["ix_producer_recommendations_decided_by_id", "ix_producer_recommendations_status", "ix_producer_recommendations_recommendation_type", "ix_producer_recommendations_content_package_id", "ix_producer_recommendations_clip_id", "ix_producer_recommendations_project_id", "ix_producer_recommendations_discovered_media_id", "ix_producer_recommendations_brand_id"]),
    ]:
        for name in names:
            op.drop_index(name, table_name=table)
    op.drop_table("producer_outcome_evaluations")
    op.drop_table("clip_quality_reports")
    op.drop_table("producer_recommendations")
