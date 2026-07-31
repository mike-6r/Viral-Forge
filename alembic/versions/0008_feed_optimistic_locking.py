"""Add explicit feed operational update versioning.

Revision ID: 0008_feed_optimistic_locking
Revises: 0007_feed_idempotency
"""

import sqlalchemy as sa

from alembic import op

revision = "0008_feed_optimistic_locking"
down_revision = "0007_feed_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "feed_subscriptions",
        sa.Column("version_id", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("feed_subscriptions", "version_id")
