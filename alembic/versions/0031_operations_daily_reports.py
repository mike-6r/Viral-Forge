"""Persist deduplicated daily operations briefing and report delivery state.

Revision ID: 0031_operations_daily_reports
Revises: 0030_operations_automation
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0031_operations_daily_reports"
down_revision = "0030_operations_automation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    default = "now()" if op.get_bind().dialect.name == "postgresql" else "CURRENT_TIMESTAMP"
    op.create_table(
        "operations_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text(default)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text(default)),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id"), nullable=False),
        sa.Column("report_type", sa.String(30), nullable=False),
        sa.Column("local_date", sa.String(10), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="PENDING_DELIVERY"),
        sa.Column("summary_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("discord_channel_id", sa.String(50)),
        sa.Column("discord_message_id", sa.String(50)),
        sa.UniqueConstraint("brand_id", "report_type", "local_date", name="uq_operations_report_brand_type_date"),
    )
    for name, column in (
        ("ix_operations_reports_brand_id", "brand_id"),
        ("ix_operations_reports_report_type", "report_type"),
        ("ix_operations_reports_local_date", "local_date"),
        ("ix_operations_reports_status", "status"),
    ):
        op.create_index(name, "operations_reports", [column])


def downgrade() -> None:
    for name in (
        "ix_operations_reports_status",
        "ix_operations_reports_local_date",
        "ix_operations_reports_report_type",
        "ix_operations_reports_brand_id",
    ):
        op.drop_index(name, table_name="operations_reports")
    op.drop_table("operations_reports")
