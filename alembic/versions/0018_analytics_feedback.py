"""Add read-only publish analytics snapshots and operator feedback.

Revision ID: 0018_analytics_feedback
Revises: 0017_publishing_foundation
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "0018_analytics_feedback"
down_revision = "0017_publishing_foundation"
branch_labels = None
depends_on = None


def _common() -> list[sa.Column[object]]:
    timestamp = "now()" if op.get_bind().dialect.name == "postgresql" else "CURRENT_TIMESTAMP"
    return [sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text(timestamp)), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text(timestamp))]


def upgrade() -> None:
    op.create_table("post_analytics_snapshots", sa.Column("publish_request_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("publish_requests.id"), nullable=False), sa.Column("clip_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_clips.id"), nullable=False), sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id"), nullable=False), sa.Column("provider", sa.String(50), nullable=False), sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False), sa.Column("collection_source", sa.String(50), nullable=False), sa.Column("views", sa.Integer()), sa.Column("watch_time_seconds", sa.Float()), sa.Column("average_view_duration_seconds", sa.Float()), sa.Column("retention_percentage", sa.Float()), sa.Column("likes", sa.Integer()), sa.Column("comments", sa.Integer()), sa.Column("shares", sa.Integer()), sa.Column("saves", sa.Integer()), sa.Column("followers_gained", sa.Integer()), sa.Column("clicks", sa.Integer()), sa.Column("platform_revenue", sa.Float()), sa.Column("currency", sa.String(10)), sa.Column("raw_metadata", sa.JSON(), nullable=False), *_common(), sa.UniqueConstraint("publish_request_id", "captured_at", name="uq_post_analytics_snapshot_time"))
    for name, column in (("ix_post_analytics_snapshots_publish_request_id", "publish_request_id"), ("ix_post_analytics_snapshots_clip_id", "clip_id"), ("ix_post_analytics_snapshots_brand_id", "brand_id"), ("ix_post_analytics_snapshots_provider", "provider"), ("ix_post_analytics_snapshots_captured_at", "captured_at")):
        op.create_index(name, "post_analytics_snapshots", [column])
    op.create_table("operator_feedback_labels", sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id"), nullable=False), sa.Column("publish_request_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("publish_requests.id"), nullable=False), sa.Column("clip_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_clips.id"), nullable=False), sa.Column("actor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False), sa.Column("label", sa.String(100), nullable=False), sa.Column("value", sa.String(255), nullable=False), sa.Column("notes", sa.Text()), *_common())
    for name, column in (("ix_operator_feedback_labels_brand_id", "brand_id"), ("ix_operator_feedback_labels_publish_request_id", "publish_request_id"), ("ix_operator_feedback_labels_clip_id", "clip_id"), ("ix_operator_feedback_labels_actor_id", "actor_id"), ("ix_operator_feedback_labels_label", "label")):
        op.create_index(name, "operator_feedback_labels", [column])
    op.create_table("analytics_refresh_runs", sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id")), sa.Column("provider", sa.String(50), nullable=False), sa.Column("status", sa.String(50), nullable=False), sa.Column("processed_count", sa.Integer(), nullable=False), sa.Column("snapshot_count", sa.Integer(), nullable=False), sa.Column("error_summary", sa.Text()), *_common())
    for name, column in (("ix_analytics_refresh_runs_brand_id", "brand_id"), ("ix_analytics_refresh_runs_provider", "provider"), ("ix_analytics_refresh_runs_status", "status")):
        op.create_index(name, "analytics_refresh_runs", [column])


def downgrade() -> None:
    op.drop_table("analytics_refresh_runs")
    op.drop_table("operator_feedback_labels")
    op.drop_table("post_analytics_snapshots")
