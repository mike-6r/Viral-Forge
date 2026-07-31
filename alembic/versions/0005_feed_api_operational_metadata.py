"""Add feed API operational metadata without changing released feed migrations.

Revision ID: 0005_feed_api_metadata
Revises: 0004_rss_atom_feed_ingestion
"""

import sqlalchemy as sa

from alembic import op

revision = "0005_feed_api_metadata"
down_revision = "0004_rss_atom_feed_ingestion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ingestion_jobs", sa.Column("result_metadata", sa.JSON()))
    op.add_column("feed_subscriptions", sa.Column("notes", sa.Text()))
    op.add_column("feed_entries", sa.Column("author", sa.String(500)))
    op.add_column("feed_entries", sa.Column("updated_at_source", sa.DateTime(timezone=True)))
    op.add_column(
        "feed_entries",
        sa.Column("import_outcome", sa.String(100), nullable=False, server_default="IMPORTED"),
    )
    op.add_column("feed_entries", sa.Column("failure_category", sa.String(100)))


def downgrade() -> None:
    for table, column in (
        ("feed_entries", "failure_category"),
        ("feed_entries", "import_outcome"),
        ("feed_entries", "updated_at_source"),
        ("feed_entries", "author"),
        ("feed_subscriptions", "notes"),
        ("ingestion_jobs", "result_metadata"),
    ):
        op.drop_column(table, column)
