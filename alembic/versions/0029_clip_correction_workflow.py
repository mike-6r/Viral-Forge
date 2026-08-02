"""Add immutable, operator-approved clip correction plans.

Revision ID: 0029_clip_correction_workflow
Revises: 0028_rendered_media_quality
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0029_clip_correction_workflow"
down_revision = "0028_rendered_media_quality"
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
        batch.add_column(sa.Column("clip_correction_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.create_table(
        "clip_correction_plans", *_timestamps(),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_projects.id"), nullable=False),
        sa.Column("source_clip_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_clips.id"), nullable=False),
        sa.Column("source_media_asset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("media_assets.id")),
        sa.Column("source_inspection_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("rendered_media_inspections.id"), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(30), nullable=False, server_default="DRAFT"),
        sa.Column("expected_review_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("approved_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("rejected_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("approved_at", sa.DateTime(timezone=True)), sa.Column("rejected_at", sa.DateTime(timezone=True)),
        sa.Column("rendering_started_at", sa.DateTime(timezone=True)), sa.Column("rendering_completed_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)), sa.Column("failure_category", sa.String(100)),
        sa.Column("operator_note", sa.Text()), sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("expected_score_improvement", sa.Float()), sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("result_clip_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_clips.id")),
        sa.Column("result_media_asset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("media_assets.id")),
        sa.Column("result_inspection_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("rendered_media_inspections.id")),
        sa.Column("renderer_config_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("comparison_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("review_version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("source_clip_id", "plan_version", name="uq_clip_correction_plan_version"),
    )
    for name, column in [("ix_clip_correction_plans_brand_id", "brand_id"), ("ix_clip_correction_plans_project_id", "project_id"), ("ix_clip_correction_plans_source_clip_id", "source_clip_id"), ("ix_clip_correction_plans_source_media_asset_id", "source_media_asset_id"), ("ix_clip_correction_plans_source_inspection_id", "source_inspection_id"), ("ix_clip_correction_plans_status", "status"), ("ix_clip_correction_plans_created_by_id", "created_by_id"), ("ix_clip_correction_plans_approved_by_id", "approved_by_id"), ("ix_clip_correction_plans_rejected_by_id", "rejected_by_id"), ("ix_clip_correction_plans_result_clip_id", "result_clip_id"), ("ix_clip_correction_plans_result_media_asset_id", "result_media_asset_id"), ("ix_clip_correction_plans_result_inspection_id", "result_inspection_id")]:
        op.create_index(name, "clip_correction_plans", [column])
    op.create_table(
        "clip_correction_actions", *_timestamps(),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clip_correction_plans.id"), nullable=False),
        sa.Column("originating_issue_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("rendered_media_inspection_issues.id")),
        sa.Column("action_order", sa.Integer(), nullable=False), sa.Column("action_type", sa.String(100), nullable=False),
        sa.Column("start_seconds", sa.Float()), sa.Column("end_seconds", sa.Float()),
        sa.Column("current_value", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("proposed_value", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("minimum_value", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("maximum_value", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""), sa.Column("evidence", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"), sa.Column("operator_selected", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("renderer_parameters", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.UniqueConstraint("plan_id", "action_order", name="uq_clip_correction_action_order"),
    )
    op.create_index("ix_clip_correction_actions_plan_id", "clip_correction_actions", ["plan_id"])
    op.create_index("ix_clip_correction_actions_originating_issue_id", "clip_correction_actions", ["originating_issue_id"])
    op.create_index("ix_clip_correction_actions_action_type", "clip_correction_actions", ["action_type"])
    with op.batch_alter_table("production_clips") as batch:
        batch.add_column(sa.Column("root_clip_id", postgresql.UUID(as_uuid=True)))
        batch.add_column(sa.Column("parent_clip_id", postgresql.UUID(as_uuid=True)))
        batch.add_column(sa.Column("revision_number", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("correction_plan_id", postgresql.UUID(as_uuid=True)))
        batch.add_column(sa.Column("superseded_by_clip_id", postgresql.UUID(as_uuid=True)))
        batch.add_column(sa.Column("is_current_operator_selection", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.create_foreign_key("fk_production_clips_root_clip", "production_clips", ["root_clip_id"], ["id"])
        batch.create_foreign_key("fk_production_clips_parent_clip", "production_clips", ["parent_clip_id"], ["id"])
        batch.create_foreign_key("fk_production_clips_correction_plan", "clip_correction_plans", ["correction_plan_id"], ["id"])
        batch.create_foreign_key("fk_production_clips_superseded_clip", "production_clips", ["superseded_by_clip_id"], ["id"])
    for name, column in [("ix_production_clips_root_clip_id", "root_clip_id"), ("ix_production_clips_parent_clip_id", "parent_clip_id"), ("ix_production_clips_correction_plan_id", "correction_plan_id"), ("ix_production_clips_superseded_by_clip_id", "superseded_by_clip_id"), ("ix_production_clips_is_current_operator_selection", "is_current_operator_selection")]:
        op.create_index(name, "production_clips", [column])


def downgrade() -> None:
    for name in ["ix_production_clips_is_current_operator_selection", "ix_production_clips_superseded_by_clip_id", "ix_production_clips_correction_plan_id", "ix_production_clips_parent_clip_id", "ix_production_clips_root_clip_id"]:
        op.drop_index(name, table_name="production_clips")
    with op.batch_alter_table("production_clips") as batch:
        for name in ["fk_production_clips_superseded_clip", "fk_production_clips_correction_plan", "fk_production_clips_parent_clip", "fk_production_clips_root_clip"]:
            batch.drop_constraint(name, type_="foreignkey")
        for column in ["is_current_operator_selection", "superseded_by_clip_id", "correction_plan_id", "revision_number", "parent_clip_id", "root_clip_id"]:
            batch.drop_column(column)
    op.drop_index("ix_clip_correction_actions_action_type", table_name="clip_correction_actions")
    op.drop_index("ix_clip_correction_actions_originating_issue_id", table_name="clip_correction_actions")
    op.drop_index("ix_clip_correction_actions_plan_id", table_name="clip_correction_actions")
    op.drop_table("clip_correction_actions")
    for name in ["ix_clip_correction_plans_result_inspection_id", "ix_clip_correction_plans_result_media_asset_id", "ix_clip_correction_plans_result_clip_id", "ix_clip_correction_plans_rejected_by_id", "ix_clip_correction_plans_approved_by_id", "ix_clip_correction_plans_created_by_id", "ix_clip_correction_plans_status", "ix_clip_correction_plans_source_inspection_id", "ix_clip_correction_plans_source_media_asset_id", "ix_clip_correction_plans_source_clip_id", "ix_clip_correction_plans_project_id", "ix_clip_correction_plans_brand_id"]:
        op.drop_index(name, table_name="clip_correction_plans")
    op.drop_table("clip_correction_plans")
    with op.batch_alter_table("content_profiles") as batch:
        batch.drop_column("clip_correction_json")
