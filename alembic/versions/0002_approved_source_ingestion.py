"""Add approved-source ingestion schema.

Revision ID: 0002_approved_source_ingestion
Revises: 0001_foundation
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002_approved_source_ingestion"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None

SOURCE_TYPE = postgresql.ENUM(
    "MANUAL_URL",
    "MANUAL_UPLOAD",
    "RSS_FEED",
    "ATOM_FEED",
    "PUBLIC_WEBPAGE",
    "OFFICIAL_API",
    "OWNER_SUBMISSION",
    "LICENSED_PROVIDER",
    "PUBLIC_RECORDS_PORTAL",
    "UNKNOWN",
    name="sourcetype",
    create_type=False,
)
SOURCE_STATUS = postgresql.ENUM(
    "PENDING_REVIEW",
    "ACTIVE",
    "PAUSED",
    "BLOCKED",
    "REJECTED",
    "ARCHIVED",
    name="sourcestatus",
    create_type=False,
)
INGESTION_METHOD = postgresql.ENUM(
    "MANUAL_URL",
    "MANUAL_UPLOAD",
    "RSS_FEED",
    "ATOM_FEED",
    name="ingestionmethod",
    create_type=False,
)
INGESTION_STATUS = postgresql.ENUM(
    "QUEUED",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "RETRY_SCHEDULED",
    "CANCELLED",
    "STALE",
    name="ingestionstatus",
    create_type=False,
)
DUPLICATE_OUTCOME = postgresql.ENUM(
    "NEW",
    "EXACT_URL_DUPLICATE",
    "CANONICAL_URL_DUPLICATE",
    "EXTERNAL_ID_DUPLICATE",
    "FILE_HASH_DUPLICATE",
    "FEED_GUID_DUPLICATE",
    "POSSIBLE_DUPLICATE",
    name="duplicateoutcome",
    create_type=False,
)
ENUMS = (SOURCE_TYPE, SOURCE_STATUS, INGESTION_METHOD, INGESTION_STATUS, DUPLICATE_OUTCOME)
JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def common() -> list[sa.Column[object]]:
    default = "now()" if op.get_bind().dialect.name == "postgresql" else "CURRENT_TIMESTAMP"
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text(default),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text(default),
        ),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for enum_type in ENUMS:
            enum_type.create(bind, checkfirst=True)
    op.add_column(
        "sources", sa.Column("source_type", SOURCE_TYPE, nullable=False, server_default="UNKNOWN")
    )
    op.add_column(
        "sources",
        sa.Column("status", SOURCE_STATUS, nullable=False, server_default="PENDING_REVIEW"),
    )
    op.create_index("ix_sources_status", "sources", ["status"])
    for column in (
        sa.Column("allowed_domains", JSONB),
        sa.Column("blocked_domains", JSONB),
        sa.Column("permitted_methods", JSONB),
        sa.Column("permitted_media_types", JSONB),
        sa.Column("max_file_size_bytes", sa.Integer(), nullable=False, server_default="104857600"),
        sa.Column("max_feed_items_per_run", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("attribution_required", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("rights_review_required", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "moderation_review_required", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "automatic_import_allowed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "manual_approval_required", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("request_timeout_seconds", sa.Integer(), nullable=False, server_default="15"),
        sa.Column("redirect_limit", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("notes", sa.Text()),
    ):
        op.add_column("source_policies", column)
    op.create_table(
        "ingestion_jobs",
        sa.Column("method", INGESTION_METHOD, nullable=False),
        sa.Column("status", INGESTION_STATUS, nullable=False),
        sa.Column(
            "actor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sources.id")),
        sa.Column("requested_url", sa.String(2048)),
        sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("error_category", sa.String(100)),
        sa.Column("error_message", sa.Text()),
        sa.Column(
            "result_content_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("content_items.id")
        ),
        sa.Column("correlation_id", sa.String(100)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("retry_at", sa.DateTime(timezone=True)),
        *common(),
    )
    op.create_table(
        "feed_subscriptions",
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sources.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("feed_url", sa.String(2048), nullable=False, unique=True),
        sa.Column("feed_type", INGESTION_METHOD, nullable=False),
        sa.Column("polling_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("etag", sa.String(255)),
        sa.Column("last_modified", sa.String(255)),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        *common(),
    )
    op.create_table(
        "feed_entries",
        sa.Column(
            "subscription_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("feed_subscriptions.id"),
            nullable=False,
        ),
        sa.Column("entry_guid", sa.String(1024), nullable=False),
        sa.Column("content_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("content_items.id")),
        sa.Column("link", sa.String(2048)),
        sa.Column("raw_metadata", JSONB),
        *common(),
        sa.UniqueConstraint("subscription_id", "entry_guid", name="uq_feed_entry_guid"),
    )
    op.create_table(
        "source_verifications",
        sa.Column(
            "source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sources.id"), nullable=False
        ),
        sa.Column("verifier_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("outcome", sa.String(50), nullable=False),
        sa.Column("evidence_reference", sa.String(2048)),
        sa.Column("notes", sa.Text()),
        *common(),
    )
    op.create_table(
        "duplicate_matches",
        sa.Column(
            "content_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_items.id"),
            nullable=False,
        ),
        sa.Column(
            "matched_content_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("content_items.id")
        ),
        sa.Column("outcome", DUPLICATE_OUTCOME, nullable=False),
        sa.Column("evidence", sa.String(2048), nullable=False),
        *common(),
    )
    for table, columns in {
        "ingestion_jobs": ("method", "status", "actor_id", "source_id", "correlation_id"),
        "feed_entries": ("subscription_id",),
        "source_verifications": ("source_id",),
        "duplicate_matches": ("content_id",),
    }.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    for table in (
        "duplicate_matches",
        "source_verifications",
        "feed_entries",
        "feed_subscriptions",
        "ingestion_jobs",
    ):
        op.drop_table(table)
    for name in (
        "notes",
        "redirect_limit",
        "request_timeout_seconds",
        "manual_approval_required",
        "automatic_import_allowed",
        "moderation_review_required",
        "rights_review_required",
        "attribution_required",
        "max_feed_items_per_run",
        "max_file_size_bytes",
        "permitted_media_types",
        "permitted_methods",
        "blocked_domains",
        "allowed_domains",
    ):
        op.drop_column("source_policies", name)
    op.drop_index("ix_sources_status", table_name="sources")
    op.drop_column("sources", "status")
    op.drop_column("sources", "source_type")
    if op.get_bind().dialect.name == "postgresql":
        for enum_type in reversed(ENUMS):
            enum_type.drop(op.get_bind(), checkfirst=True)
