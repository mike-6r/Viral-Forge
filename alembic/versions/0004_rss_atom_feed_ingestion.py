"""Add RSS and Atom feed operational state.

Revision ID: 0004_rss_atom_feed_ingestion
Revises: 0003_secure_media_uploads
"""

import sqlalchemy as sa

from alembic import op

revision = "0004_rss_atom_feed_ingestion"
down_revision = "0003_secure_media_uploads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table, columns in {
        "feed_subscriptions": (
            sa.Column("status", sa.String(50), nullable=False, server_default="PENDING_VALIDATION"),
            sa.Column("final_url", sa.String(2048)),
            sa.Column("title", sa.String(500)),
            sa.Column("description", sa.Text()),
            sa.Column("language", sa.String(50)),
            sa.Column("site_url", sa.String(2048)),
            sa.Column("last_error_category", sa.String(100)),
            sa.Column("last_error_message", sa.Text()),
            sa.Column("recent_item_window_days", sa.Integer(), nullable=False, server_default="30"),
            sa.Column("max_items_per_run", sa.Integer(), nullable=False, server_default="20"),
            sa.Column("active_job_id", sa.String(36)),
            sa.Column("active_lease_until", sa.DateTime(timezone=True)),
            sa.Column("correlation_id", sa.String(100)),
        ),
        "feed_entries": (
            sa.Column("identity_strategy", sa.String(50), nullable=False, server_default="GUID"),
            sa.Column("published_at", sa.DateTime(timezone=True)),
            sa.Column("title", sa.String(500)),
        ),
    }.items():
        for column in columns:
            op.add_column(table, column)
    op.create_index("ix_feed_subscriptions_status", "feed_subscriptions", ["status"])
    op.create_index(
        "ix_feed_subscriptions_correlation_id", "feed_subscriptions", ["correlation_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_feed_subscriptions_correlation_id", table_name="feed_subscriptions")
    op.drop_index("ix_feed_subscriptions_status", table_name="feed_subscriptions")
    for table, names in {
        "feed_entries": ("title", "published_at", "identity_strategy"),
        "feed_subscriptions": (
            "correlation_id",
            "active_lease_until",
            "active_job_id",
            "max_items_per_run",
            "recent_item_window_days",
            "last_error_message",
            "last_error_category",
            "site_url",
            "language",
            "description",
            "title",
            "final_url",
            "status",
        ),
    }.items():
        for name in names:
            op.drop_column(table, name)
