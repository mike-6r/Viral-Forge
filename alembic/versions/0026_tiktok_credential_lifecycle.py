"""Add opaque TikTok credential lifecycle references.

Revision ID: 0026_tiktok_credential_lifecycle
Revises: 0025_tiktok_publishing_provider
"""

import sqlalchemy as sa

from alembic import op

revision = "0026_tiktok_credential_lifecycle"
down_revision = "0025_tiktok_publishing_provider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("publishing_account_connections") as batch:
        batch.add_column(sa.Column("granted_scopes", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
        batch.add_column(sa.Column("credential_expires_at", sa.String(64)))
    op.create_index(
        "ix_publishing_account_connections_credential_expires_at",
        "publishing_account_connections",
        ["credential_expires_at"],
    )
    with op.batch_alter_table("tiktok_oauth_states") as batch:
        batch.add_column(sa.Column("pkce_verifier_reference", sa.String(500)))


def downgrade() -> None:
    with op.batch_alter_table("tiktok_oauth_states") as batch:
        batch.drop_column("pkce_verifier_reference")
    op.drop_index("ix_publishing_account_connections_credential_expires_at", table_name="publishing_account_connections")
    with op.batch_alter_table("publishing_account_connections") as batch:
        batch.drop_column("credential_expires_at")
        batch.drop_column("granted_scopes")
