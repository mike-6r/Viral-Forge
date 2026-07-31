"""Add durable manual-upload asset metadata.

Revision ID: 0003_secure_media_uploads
Revises: 0002_approved_source_ingestion
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0003_secure_media_uploads"
down_revision = "0002_approved_source_ingestion"
branch_labels = None
depends_on = None

JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("ingestion_jobs") as batch:
        batch.add_column(sa.Column("result_asset_id", postgresql.UUID(as_uuid=True)))
        batch.create_foreign_key(
            "fk_ingestion_jobs_result_asset", "media_assets", ["result_asset_id"], ["id"]
        )
    with op.batch_alter_table("media_assets") as batch:
        for column in (
            sa.Column("storage_provider", sa.String(50), nullable=False, server_default="local"),
            sa.Column("original_filename", sa.String(255)),
            sa.Column("display_filename", sa.String(255)),
            sa.Column("detected_media_type", sa.String(100)),
            sa.Column("declared_media_type", sa.String(100)),
            sa.Column("container_type", sa.String(50)),
            sa.Column("file_size_bytes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("uploader_id", postgresql.UUID(as_uuid=True)),
            sa.Column("source_id", postgresql.UUID(as_uuid=True)),
            sa.Column(
                "asset_status",
                sa.String(50),
                nullable=False,
                server_default="VERIFICATION_REQUIRED",
            ),
            sa.Column("correlation_id", sa.String(100)),
            sa.Column("storage_metadata", JSONB),
        ):
            batch.add_column(column)
        batch.create_foreign_key("fk_media_assets_uploader", "users", ["uploader_id"], ["id"])
        batch.create_foreign_key("fk_media_assets_source", "sources", ["source_id"], ["id"])
    for column in ("uploader_id", "source_id", "asset_status", "correlation_id"):
        op.create_index(f"ix_media_assets_{column}", "media_assets", [column])
    op.create_index("uq_media_assets_checksum", "media_assets", ["checksum"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_media_assets_checksum", table_name="media_assets")
    for column in ("correlation_id", "asset_status", "source_id", "uploader_id"):
        op.drop_index(f"ix_media_assets_{column}", table_name="media_assets")
    with op.batch_alter_table("media_assets") as batch:
        batch.drop_constraint("fk_media_assets_source", type_="foreignkey")
        batch.drop_constraint("fk_media_assets_uploader", type_="foreignkey")
        for column in (
            "storage_metadata",
            "correlation_id",
            "asset_status",
            "source_id",
            "uploader_id",
            "file_size_bytes",
            "container_type",
            "declared_media_type",
            "detected_media_type",
            "display_filename",
            "original_filename",
            "storage_provider",
        ):
            batch.drop_column(column)
    with op.batch_alter_table("ingestion_jobs") as batch:
        batch.drop_constraint("fk_ingestion_jobs_result_asset", type_="foreignkey")
        batch.drop_column("result_asset_id")
