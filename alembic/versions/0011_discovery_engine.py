"""Add public-video discovery sources, media records, and runs.

Revision ID: 0011_discovery_engine
Revises: 0010_source_quality_review
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0011_discovery_engine"
down_revision = "0010_source_quality_review"
branch_labels = None
depends_on = None


def _common() -> list[sa.Column[object]]:
    timestamp = "now()" if op.get_bind().dialect.name == "postgresql" else "CURRENT_TIMESTAMP"
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text(timestamp),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text(timestamp),
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "discovery_sources",
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("platform", sa.String(50), nullable=False),
        sa.Column("agency_reference", sa.String(500)),
        sa.Column("account_identifier", sa.String(500)),
        sa.Column("public_url", sa.String(2048), nullable=False, unique=True),
        sa.Column("country", sa.String(100)),
        sa.Column("state_region", sa.String(255)),
        sa.Column("jurisdiction", sa.String(500)),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("trusted", sa.Boolean(), nullable=False),
        sa.Column("polling_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("last_attempted_poll_at", sa.DateTime(timezone=True)),
        sa.Column("last_successful_poll_at", sa.DateTime(timezone=True)),
        sa.Column("next_poll_at", sa.DateTime(timezone=True)),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("last_error_category", sa.String(100)),
        sa.Column("configuration_json", sa.JSON(), nullable=False),
        *_common(),
    )
    op.create_table(
        "discovery_runs",
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column(
            "discovery_source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("discovery_sources.id"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("fetched_count", sa.Integer(), nullable=False),
        sa.Column("new_count", sa.Integer(), nullable=False),
        sa.Column("duplicate_count", sa.Integer(), nullable=False),
        sa.Column("skipped_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("error_summary", sa.Text()),
        sa.Column("cursor", sa.String(500)),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        *_common(),
    )
    op.create_table(
        "discovered_media",
        sa.Column(
            "discovery_source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("discovery_sources.id"),
            nullable=False,
        ),
        sa.Column("provider_item_id", sa.String(500), nullable=False),
        sa.Column("canonical_url", sa.String(2048), nullable=False),
        sa.Column("submitted_url", sa.String(2048), nullable=False),
        sa.Column("platform", sa.String(50), nullable=False),
        sa.Column("title", sa.String(500)),
        sa.Column("description", sa.Text()),
        sa.Column("uploader", sa.String(500)),
        sa.Column("uploader_id", sa.String(255)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("thumbnail_url", sa.String(2048)),
        sa.Column("view_count", sa.Integer()),
        sa.Column("language", sa.String(50)),
        sa.Column("location_hints", sa.JSON(), nullable=False),
        sa.Column("agency_hints", sa.JSON(), nullable=False),
        sa.Column("incident_hints", sa.JSON(), nullable=False),
        sa.Column("discovery_score", sa.Float(), nullable=False),
        sa.Column("quality_score", sa.Float()),
        sa.Column("source_confidence", sa.Float()),
        sa.Column("watermark_status", sa.String(50), nullable=False),
        sa.Column("duplicate_status", sa.String(50), nullable=False),
        sa.Column("lifecycle_status", sa.String(50), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column(
            "production_project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("production_projects.id"),
        ),
        sa.Column("review_version", sa.Integer(), nullable=False),
        *_common(),
        sa.UniqueConstraint(
            "discovery_source_id", "provider_item_id", name="uq_discovered_media_provider_item"
        ),
    )
    for table, column in (
        ("discovery_sources", "provider"),
        ("discovery_sources", "enabled"),
        ("discovery_sources", "next_poll_at"),
        ("discovery_runs", "provider"),
        ("discovery_runs", "discovery_source_id"),
        ("discovery_runs", "status"),
        ("discovered_media", "discovery_source_id"),
        ("discovered_media", "canonical_url"),
        ("discovered_media", "discovered_at"),
        ("discovered_media", "duplicate_status"),
        ("discovered_media", "lifecycle_status"),
        ("discovered_media", "production_project_id"),
    ):
        op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    for table in ("discovered_media", "discovery_runs", "discovery_sources"):
        op.drop_table(table)
