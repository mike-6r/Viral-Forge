"""Add durable private-preview grants and retained production-media inventory.

Revision ID: 0021_media_preview
Revises: 0020_checksum_index
"""

import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0021_media_preview"
down_revision = "0020_checksum_index"
branch_labels = None
depends_on = None
LEGACY_BRAND_ID = uuid.UUID("4e6768ac-d9bc-4eac-8f30-e73ffc510102")


def _uuid_type():  # type: ignore[no-untyped-def]
    return (
        postgresql.UUID(as_uuid=True) if op.get_bind().dialect.name == "postgresql" else sa.Uuid()
    )


def _columns(uuid_type):  # type: ignore[no-untyped-def]
    return (
        sa.Column("brand_id", uuid_type, nullable=True),
        sa.Column("project_id", uuid_type, nullable=True),
        sa.Column("clip_id", uuid_type, nullable=True),
        sa.Column("asset_type", sa.String(50), nullable=False, server_default="UPLOAD_ASSET"),
        sa.Column("content_type", sa.String(100)),
        sa.Column("lifecycle_state", sa.String(50), nullable=False, server_default="ACTIVE"),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True)),
        sa.Column("retention_deadline", sa.DateTime(timezone=True)),
        sa.Column("administrative_hold", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("deletion_reason", sa.Text()),
        sa.Column("former_size_bytes", sa.Integer()),
        sa.Column("deletion_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deletion_error", sa.Text()),
        sa.Column("version_id", sa.Integer(), nullable=False, server_default="1"),
    )


def _add_media_columns(uuid_type):  # type: ignore[no-untyped-def]
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.alter_column("media_assets", "content_id", existing_type=uuid_type, nullable=True)
        for column in _columns(uuid_type):
            op.add_column("media_assets", column)
        op.create_foreign_key(
            "fk_media_assets_brand_id_brands", "media_assets", "brands", ["brand_id"], ["id"]
        )
        op.create_foreign_key(
            "fk_media_assets_project_id_production_projects",
            "media_assets",
            "production_projects",
            ["project_id"],
            ["id"],
        )
        op.create_foreign_key(
            "fk_media_assets_clip_id_production_clips",
            "media_assets",
            "production_clips",
            ["clip_id"],
            ["id"],
        )
    else:
        with op.batch_alter_table("media_assets", recreate="always") as batch:
            batch.alter_column("content_id", existing_type=uuid_type, nullable=True)
            for column in _columns(uuid_type):
                batch.add_column(column)
            batch.create_foreign_key(
                "fk_media_assets_brand_id_brands", "brands", ["brand_id"], ["id"]
            )
            batch.create_foreign_key(
                "fk_media_assets_project_id_production_projects",
                "production_projects",
                ["project_id"],
                ["id"],
            )
            batch.create_foreign_key(
                "fk_media_assets_clip_id_production_clips", "production_clips", ["clip_id"], ["id"]
            )


def _finalize_media_columns(uuid_type):  # type: ignore[no-untyped-def]
    changes = (
        ("brand_id", uuid_type, {"nullable": False}),
        ("asset_type", sa.String(50), {"server_default": None}),
        ("lifecycle_state", sa.String(50), {"server_default": None}),
        ("administrative_hold", sa.Boolean(), {"server_default": None}),
        ("deletion_attempts", sa.Integer(), {"server_default": None}),
        ("version_id", sa.Integer(), {"server_default": None}),
    )
    if op.get_bind().dialect.name == "postgresql":
        for name, type_, kwargs in changes:
            op.alter_column("media_assets", name, existing_type=type_, **kwargs)
    else:
        with op.batch_alter_table("media_assets", recreate="always") as batch:
            for name, type_, kwargs in changes:
                batch.alter_column(name, existing_type=type_, **kwargs)


def upgrade() -> None:
    bind, uuid_type = op.get_bind(), _uuid_type()
    _add_media_columns(uuid_type)
    media = sa.table("media_assets", sa.column("brand_id", uuid_type))
    bind.execute(media.update().where(media.c.brand_id.is_(None)).values(brand_id=LEGACY_BRAND_ID))
    for name, columns in (
        ("ix_media_assets_brand_id", ["brand_id"]),
        ("ix_media_assets_project_id", ["project_id"]),
        ("ix_media_assets_clip_id", ["clip_id"]),
        ("ix_media_assets_asset_type", ["asset_type"]),
        ("ix_media_assets_lifecycle_state", ["lifecycle_state"]),
        ("ix_media_assets_retention_deadline", ["retention_deadline"]),
        ("ix_media_assets_administrative_hold", ["administrative_hold"]),
        ("ix_media_assets_clip_type", ["clip_id", "asset_type"]),
    ):
        op.create_index(name, "media_assets", columns)
    op.create_table(
        "preview_grants",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("brand_id", uuid_type, sa.ForeignKey("brands.id"), nullable=False),
        sa.Column("project_id", uuid_type, sa.ForeignKey("production_projects.id"), nullable=False),
        sa.Column("clip_id", uuid_type, sa.ForeignKey("production_clips.id"), nullable=False),
        sa.Column("media_asset_id", uuid_type, sa.ForeignKey("media_assets.id"), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True),
        sa.Column("purpose", sa.String(50), nullable=False),
        sa.Column("created_by_id", uuid_type, sa.ForeignKey("users.id")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("access_count", sa.Integer(), nullable=False),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True)),
        sa.Column("maximum_access_count", sa.Integer()),
        sa.Column("version_id", sa.Integer(), nullable=False),
    )
    for name, columns in (
        ("ix_preview_grants_brand_id", ["brand_id"]),
        ("ix_preview_grants_project_id", ["project_id"]),
        ("ix_preview_grants_clip_id", ["clip_id"]),
        ("ix_preview_grants_media_asset_id", ["media_asset_id"]),
        ("ix_preview_grants_created_by_id", ["created_by_id"]),
        ("ix_preview_grants_expires_at", ["expires_at"]),
        ("ix_preview_grants_revoked_at", ["revoked_at"]),
        ("ix_preview_grants_active_lookup", ["clip_id", "revoked_at", "expires_at"]),
    ):
        op.create_index(name, "preview_grants", columns)
    clips = bind.execute(
        sa.text(
            "SELECT id, project_id, brand_id, storage_key, approval_status FROM production_clips WHERE render_status = 'SUCCEEDED' AND storage_key IS NOT NULL"
        )
    ).mappings()
    existing = {row[0] for row in bind.execute(sa.text("SELECT storage_key FROM media_assets"))}
    assets = sa.table(
        "media_assets",
        sa.column("id", uuid_type),
        sa.column("brand_id", uuid_type),
        sa.column("project_id", uuid_type),
        sa.column("clip_id", uuid_type),
        sa.column("storage_key", sa.String()),
        sa.column("media_type", sa.String()),
        sa.column("content_type", sa.String()),
        sa.column("file_size_bytes", sa.Integer()),
        sa.column("storage_provider", sa.String()),
        sa.column("asset_type", sa.String()),
        sa.column("lifecycle_state", sa.String()),
        sa.column("retention_deadline", sa.DateTime(timezone=True)),
        sa.column("version_id", sa.Integer()),
    )
    deadline = datetime.now(UTC) + timedelta(days=3)
    for clip in clips:
        if clip["storage_key"] not in existing:
            bind.execute(
                assets.insert().values(
                    id=uuid.uuid4(),
                    brand_id=clip["brand_id"],
                    project_id=clip["project_id"],
                    clip_id=clip["id"],
                    storage_key=clip["storage_key"],
                    media_type="video",
                    content_type="video/mp4",
                    file_size_bytes=0,
                    storage_provider="local",
                    asset_type="RENDERED_CLIP",
                    lifecycle_state="PENDING_REVIEW"
                    if clip["approval_status"] == "PENDING"
                    else clip["approval_status"],
                    retention_deadline=deadline,
                    version_id=1,
                )
            )
    _finalize_media_columns(uuid_type)


def downgrade() -> None:
    op.drop_table("preview_grants")
    names = (
        "ix_media_assets_clip_type",
        "ix_media_assets_administrative_hold",
        "ix_media_assets_retention_deadline",
        "ix_media_assets_lifecycle_state",
        "ix_media_assets_asset_type",
        "ix_media_assets_clip_id",
        "ix_media_assets_project_id",
        "ix_media_assets_brand_id",
    )
    for name in names:
        op.drop_index(name, table_name="media_assets")
    op.execute(sa.text("DELETE FROM media_assets WHERE content_id IS NULL"))
    columns = (
        "version_id",
        "deletion_error",
        "deletion_attempts",
        "former_size_bytes",
        "deletion_reason",
        "deleted_at",
        "administrative_hold",
        "retention_deadline",
        "last_accessed_at",
        "lifecycle_state",
        "content_type",
        "asset_type",
        "clip_id",
        "project_id",
        "brand_id",
    )
    constraints = (
        "fk_media_assets_clip_id_production_clips",
        "fk_media_assets_project_id_production_projects",
        "fk_media_assets_brand_id_brands",
    )
    if op.get_bind().dialect.name == "postgresql":
        for name in constraints:
            op.drop_constraint(name, "media_assets", type_="foreignkey")
        for name in columns:
            op.drop_column("media_assets", name)
        op.alter_column("media_assets", "content_id", existing_type=_uuid_type(), nullable=False)
    else:
        with op.batch_alter_table("media_assets", recreate="always") as batch:
            for name in constraints:
                batch.drop_constraint(name, type_="foreignkey")
            for name in columns:
                batch.drop_column(name)
            batch.alter_column("content_id", existing_type=_uuid_type(), nullable=False)
