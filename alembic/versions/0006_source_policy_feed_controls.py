"""Add explicit source-policy controls for bounded feed ingestion.

Revision ID: 0006_source_policy_feed_controls
Revises: 0005_feed_api_metadata
"""

import sqlalchemy as sa

from alembic import op

revision = "0006_source_policy_feed_controls"
down_revision = "0005_feed_api_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("source_policies", sa.Column("feed_recent_window_days", sa.Integer()))
    op.add_column("source_policies", sa.Column("min_feed_run_interval_seconds", sa.Integer()))


def downgrade() -> None:
    op.drop_column("source_policies", "min_feed_run_interval_seconds")
    op.drop_column("source_policies", "feed_recent_window_days")
