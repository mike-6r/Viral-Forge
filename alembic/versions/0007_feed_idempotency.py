"""Persist feed registration idempotency keys.

Revision ID: 0007_feed_idempotency
Revises: 0006_source_policy_feed_controls
"""

import sqlalchemy as sa

from alembic import op

revision = "0007_feed_idempotency"
down_revision = "0006_source_policy_feed_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("feed_subscriptions", sa.Column("idempotency_key", sa.String(255)))
    with op.batch_alter_table("feed_subscriptions") as batch:
        batch.create_unique_constraint("uq_feed_subscriptions_idempotency_key", ["idempotency_key"])


def downgrade() -> None:
    with op.batch_alter_table("feed_subscriptions") as batch:
        batch.drop_constraint("uq_feed_subscriptions_idempotency_key", type_="unique")
    op.drop_column("feed_subscriptions", "idempotency_key")
