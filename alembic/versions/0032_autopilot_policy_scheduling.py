"""Add policy-governed unattended operations state.

Revision ID: 0032_autopilot_policy_scheduling
Revises: 0031_operations_daily_reports
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0032_autopilot_policy_scheduling"
down_revision = "0031_operations_daily_reports"
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
        "autopilot_global_controls",
        *_timestamps(),
        sa.Column("control_key", sa.String(50), nullable=False, unique=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("emergency_stop", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("discovery_paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("processing_paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("publishing_paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
    )
    op.create_table(
        "autopilot_policies", *_timestamps(),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id"), nullable=False),
        sa.Column("automation_level", sa.String(32), nullable=False, server_default="MANUAL"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("config_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.UniqueConstraint("brand_id", name="uq_autopilot_policy_brand"),
    )
    op.create_table(
        "autopilot_decisions", *_timestamps(),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id"), nullable=False),
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("autopilot_policies.id"), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False), sa.Column("action", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False), sa.Column("object_type", sa.String(100), nullable=False),
        sa.Column("object_id", sa.String(64), nullable=False), sa.Column("reason_codes", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("explanation", sa.Text(), nullable=False), sa.Column("evidence_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("thresholds_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("actuals_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("missing_evidence", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("confidence", sa.Integer()), sa.Column("correlation_key", sa.String(255)),
    )
    op.create_table(
        "autopilot_runs", *_timestamps(),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id"), nullable=False),
        sa.Column("object_type", sa.String(100), nullable=False), sa.Column("object_id", sa.String(64), nullable=False),
        sa.Column("stage", sa.String(64), nullable=False), sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("heartbeat_at", sa.String(64)),
        sa.Column("recovery_class", sa.String(32)), sa.Column("last_error", sa.Text()),
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("autopilot_decisions.id")),
        sa.UniqueConstraint("brand_id", "object_type", "object_id", "stage", name="uq_autopilot_run_stage"),
    )
    op.create_table(
        "autopilot_schedule_slots", *_timestamps(),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id"), nullable=False),
        sa.Column("destination_account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("destination_accounts.id"), nullable=False),
        sa.Column("queue_item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("posting_queue_items.id"), nullable=False, unique=True),
        sa.Column("clip_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_clips.id"), nullable=False),
        sa.Column("content_package_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("content_packages.id"), nullable=False),
        sa.Column("content_package_generation_version", sa.Integer(), nullable=False), sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("scheduled_for", sa.String(64), nullable=False), sa.Column("provider_mode", sa.String(50), nullable=False, server_default="HUMAN_CONFIRMATION"),
        sa.Column("privacy", sa.String(50), nullable=False, server_default="private"), sa.Column("status", sa.String(32), nullable=False, server_default="RESERVED"),
        sa.Column("confirmation_required", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("hold_reason", sa.Text()),
        sa.UniqueConstraint("destination_account_id", "scheduled_for", name="uq_autopilot_destination_slot"),
    )
    op.create_table(
        "autopilot_queue_ranks", *_timestamps(),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id"), nullable=False),
        sa.Column("queue_item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("posting_queue_items.id"), nullable=False),
        sa.Column("rank_score", sa.Integer(), nullable=False), sa.Column("rank_position", sa.Integer()), sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
        sa.Column("explanation", sa.Text(), nullable=False), sa.Column("evidence_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")), sa.Column("manual_override", sa.String(32)),
        sa.UniqueConstraint("queue_item_id", name="uq_autopilot_queue_rank_item"),
    )
    op.create_table(
        "autopilot_exceptions", *_timestamps(),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id"), nullable=False), sa.Column("decision_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("autopilot_decisions.id")),
        sa.Column("category", sa.String(80), nullable=False), sa.Column("severity", sa.String(20), nullable=False, server_default="WARNING"), sa.Column("dedupe_key", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="OPEN"), sa.Column("object_type", sa.String(100), nullable=False), sa.Column("object_id", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False), sa.Column("recommended_action", sa.String(255), nullable=False), sa.Column("retry_state", sa.String(32), nullable=False, server_default="OPERATOR_REQUIRED"), sa.Column("occurrences", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("brand_id", "dedupe_key", "status", name="uq_autopilot_exception_dedupe"),
    )
    for table, columns in {
        "autopilot_policies": ["brand_id", "automation_level", "is_paused"],
        "autopilot_decisions": ["brand_id", "policy_id", "action", "decision", "object_type", "object_id", "correlation_key"],
        "autopilot_runs": ["brand_id", "stage", "status", "heartbeat_at"],
        "autopilot_schedule_slots": ["brand_id", "destination_account_id", "clip_id", "content_package_id", "scheduled_for", "status"],
        "autopilot_queue_ranks": ["brand_id", "queue_item_id", "rank_score", "status"],
        "autopilot_exceptions": ["brand_id", "decision_id", "category", "severity", "status"],
    }.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    tables = {
        "autopilot_exceptions": ["brand_id", "decision_id", "category", "severity", "status"],
        "autopilot_queue_ranks": ["brand_id", "queue_item_id", "rank_score", "status"],
        "autopilot_schedule_slots": ["brand_id", "destination_account_id", "clip_id", "content_package_id", "scheduled_for", "status"],
        "autopilot_runs": ["brand_id", "stage", "status", "heartbeat_at"],
        "autopilot_decisions": ["brand_id", "policy_id", "action", "decision", "object_type", "object_id", "correlation_key"],
        "autopilot_policies": ["brand_id", "automation_level", "is_paused"],
    }
    for table, columns in tables.items():
        for column in columns:
            op.drop_index(f"ix_{table}_{column}", table_name=table)
        op.drop_table(table)
    op.drop_table("autopilot_global_controls")
