"""Add brand-scoped operations schedules, alerts, and operator tasks.

Revision ID: 0030_operations_automation
Revises: 0029_clip_correction_workflow
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0030_operations_automation"
down_revision = "0029_clip_correction_workflow"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column[object]]:
    default = "now()" if op.get_bind().dialect.name == "postgresql" else "CURRENT_TIMESTAMP"
    return [sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text(default)), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text(default))]


def upgrade() -> None:
    with op.batch_alter_table("content_profiles") as batch:
        batch.add_column(sa.Column("operations_schedule_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.create_table("operations_alerts", *_timestamps(), sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id"), nullable=False), sa.Column("severity", sa.String(20), nullable=False, server_default="WARNING"), sa.Column("category", sa.String(80), nullable=False), sa.Column("dedupe_key", sa.String(255), nullable=False), sa.Column("status", sa.String(30), nullable=False, server_default="OPEN"), sa.Column("summary", sa.Text(), nullable=False), sa.Column("details_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")), sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False), sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False), sa.Column("resolved_at", sa.DateTime(timezone=True)), sa.Column("occurrences", sa.Integer(), nullable=False, server_default="1"), sa.UniqueConstraint("brand_id", "dedupe_key", "status", name="uq_operations_alert_dedupe"))
    op.create_table("operator_tasks", *_timestamps(), sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id"), nullable=False), sa.Column("priority", sa.String(20), nullable=False, server_default="NORMAL"), sa.Column("task_type", sa.String(80), nullable=False), sa.Column("dedupe_key", sa.String(255), nullable=False), sa.Column("status", sa.String(30), nullable=False, server_default="OPEN"), sa.Column("title", sa.String(255), nullable=False), sa.Column("reason", sa.Text(), nullable=False), sa.Column("action_label", sa.String(255), nullable=False), sa.Column("payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")), sa.Column("completed_at", sa.DateTime(timezone=True)), sa.UniqueConstraint("brand_id", "dedupe_key", "status", name="uq_operator_task_dedupe"))
    for table, pairs in {"operations_alerts": [("ix_operations_alerts_brand_id", "brand_id"), ("ix_operations_alerts_severity", "severity"), ("ix_operations_alerts_category", "category"), ("ix_operations_alerts_status", "status")], "operator_tasks": [("ix_operator_tasks_brand_id", "brand_id"), ("ix_operator_tasks_priority", "priority"), ("ix_operator_tasks_task_type", "task_type"), ("ix_operator_tasks_status", "status")]}.items():
        for name, column in pairs: op.create_index(name, table, [column])


def downgrade() -> None:
    for table, names in {"operator_tasks": ["ix_operator_tasks_status", "ix_operator_tasks_task_type", "ix_operator_tasks_priority", "ix_operator_tasks_brand_id"], "operations_alerts": ["ix_operations_alerts_status", "ix_operations_alerts_category", "ix_operations_alerts_severity", "ix_operations_alerts_brand_id"]}.items():
        for name in names: op.drop_index(name, table_name=table)
    op.drop_table("operator_tasks")
    op.drop_table("operations_alerts")
    with op.batch_alter_table("content_profiles") as batch:
        batch.drop_column("operations_schedule_json")
