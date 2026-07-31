"""Remove the redundant non-unique checksum index while preserving uniqueness.

Revision ID: 0020_checksum_index
Revises: 0019_schema_hardening
"""

from alembic import op

revision = "0020_checksum_index"
down_revision = "0019_schema_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_media_assets_checksum", table_name="media_assets")


def downgrade() -> None:
    op.create_index("ix_media_assets_checksum", "media_assets", ["checksum"])
