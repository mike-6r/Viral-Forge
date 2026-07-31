"""Create the immutable ViralForge foundation schema.

Revision ID: 0001_foundation
Revises:
Create Date: 2026-07-30

Corrected before shared deployment: the original unreleased implementation
incorrectly delegated schema creation to live ORM metadata.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None

CONTENT_STATUS = postgresql.ENUM(
    "DISCOVERED",
    "IMPORTED",
    "SOURCE_VERIFICATION_REQUIRED",
    "RIGHTS_REVIEW_REQUIRED",
    "MODERATION_REQUIRED",
    "READY_FOR_RANKING",
    "RANKED",
    "PROCESSING_QUEUED",
    "PROCESSING",
    "REVIEW_REQUIRED",
    "APPROVED",
    "REJECTED",
    "SCHEDULED",
    "PUBLISHING",
    "PUBLISHED",
    "PUBLISH_FAILED",
    "ARCHIVED",
    "BLOCKED",
    name="contentstatus",
    create_type=False,
)
PLATFORM = postgresql.ENUM(
    "MANUAL",
    "TIKTOK",
    "YOUTUBE",
    "INSTAGRAM",
    "FACEBOOK",
    "OTHER",
    name="platform",
    create_type=False,
)
JOB_STATUS = postgresql.ENUM(
    "QUEUED",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "STALE",
    name="jobstatus",
    create_type=False,
)
ROLE_NAME = postgresql.ENUM(
    "OWNER",
    "ADMIN",
    "EDITOR",
    "REVIEWER",
    "ANALYST",
    "VIEWER",
    "SYSTEM",
    name="rolename",
    create_type=False,
)
RIGHTS_STATE = postgresql.ENUM(
    "UNKNOWN",
    "OWNER_SUBMITTED",
    "LICENSED",
    "PUBLIC_DOMAIN",
    "PERMISSION_GRANTED",
    "PLATFORM_REUSE_ALLOWED",
    "ATTRIBUTION_REQUIRED",
    "RESTRICTED",
    "DISPUTED",
    "DENIED",
    name="rightsstate",
    create_type=False,
)
RIGHTS_DISPOSITION = postgresql.ENUM(
    "PENDING",
    "APPROVED",
    "REJECTED",
    "REQUIRES_REVIEW",
    name="rightsdisposition",
    create_type=False,
)
MODERATION_RISK = postgresql.ENUM(
    "GRAPHIC_VIOLENCE",
    "DEATH_OR_SERIOUS_INJURY",
    "MINORS",
    "PERSONAL_INFORMATION",
    "SEXUAL_CONTENT",
    "HATE_OR_HARASSMENT",
    "SELF_HARM",
    "ILLEGAL_ACTIVITY",
    "ACTIVE_INVESTIGATION",
    "MISLEADING_CONTEXT",
    "SENSITIVE_LOCATION",
    "OTHER",
    name="moderationrisk",
    create_type=False,
)
MODERATION_DISPOSITION = postgresql.ENUM(
    "PENDING",
    "APPROVED",
    "REJECTED",
    "REQUIRES_REVIEW",
    name="moderationdisposition",
    create_type=False,
)
REVIEW_OUTCOME = postgresql.ENUM("APPROVED", "REJECTED", name="reviewoutcome", create_type=False)
ENUMS = (
    CONTENT_STATUS,
    PLATFORM,
    JOB_STATUS,
    ROLE_NAME,
    RIGHTS_STATE,
    RIGHTS_DISPOSITION,
    MODERATION_RISK,
    MODERATION_DISPOSITION,
    REVIEW_OUTCOME,
)
JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _common_columns() -> list[sa.Column[object]]:
    timestamp_default = (
        "now()" if op.get_bind().dialect.name == "postgresql" else "CURRENT_TIMESTAMP"
    )
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text(timestamp_default),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text(timestamp_default),
        ),
    ]


def _create_enum_types() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for enum_type in ENUMS:
            enum_type.create(bind, checkfirst=True)


def upgrade() -> None:
    _create_enum_types()
    op.create_table(
        "content_items",
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("status", CONTENT_STATUS, nullable=False),
        sa.Column("source_provenance_complete", sa.Boolean(), nullable=False),
        sa.Column("external_reference", sa.String(255)),
        sa.Column("version_id", sa.Integer(), nullable=False),
        *_common_columns(),
    )
    op.create_table(
        "platform_accounts",
        sa.Column("platform", PLATFORM, nullable=False),
        sa.Column("external_account_id", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        *_common_columns(),
        sa.UniqueConstraint("platform", "external_account_id", name="uq_platform_account"),
    )
    op.create_table(
        "roles", sa.Column("name", ROLE_NAME, nullable=False, unique=True), *_common_columns()
    )
    op.create_table(
        "sources",
        sa.Column("platform", PLATFORM, nullable=False),
        sa.Column("external_id", sa.String(255)),
        sa.Column("normalized_url", sa.String(2048), nullable=False, unique=True),
        sa.Column("uploader_name", sa.String(255)),
        sa.Column("published_at_source", sa.String(64)),
        sa.Column("provider_metadata", JSONB),
        *_common_columns(),
        sa.UniqueConstraint("platform", "external_id", name="uq_source_platform_external"),
    )
    op.create_table(
        "users",
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_common_columns(),
    )
    op.create_table(
        "audit_events",
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("entity_type", sa.String(100), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_name", sa.String(150), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("correlation_id", sa.String(100)),
        sa.Column("payload", JSONB),
        *_common_columns(),
    )
    op.create_table(
        "clip_candidates",
        sa.Column(
            "content_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_items.id"),
            nullable=False,
        ),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        *_common_columns(),
    )
    op.create_table(
        "content_sources",
        sa.Column(
            "content_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_items.id"),
            nullable=False,
        ),
        sa.Column(
            "source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sources.id"), nullable=False
        ),
        sa.Column("is_original", sa.Boolean(), nullable=False),
        sa.Column("source_url", sa.String(2048), nullable=False),
        *_common_columns(),
        sa.UniqueConstraint("content_id", "source_id", name="uq_content_source"),
    )
    op.create_table(
        "media_assets",
        sa.Column(
            "content_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_items.id"),
            nullable=False,
        ),
        sa.Column("storage_key", sa.String(1024), nullable=False, unique=True),
        sa.Column("media_type", sa.String(100), nullable=False),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("checksum", sa.String(128)),
        *_common_columns(),
    )
    op.create_table(
        "moderation_assessments",
        sa.Column(
            "content_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_items.id"),
            nullable=False,
        ),
        sa.Column("risk_category", MODERATION_RISK, nullable=False),
        sa.Column("disposition", MODERATION_DISPOSITION, nullable=False),
        sa.Column("is_automatic", sa.Boolean(), nullable=False),
        sa.Column("reviewer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("reviewer_notes", sa.Text()),
        sa.Column("evidence_reference", sa.String(2048)),
        sa.Column("policy_version", sa.String(100), nullable=False),
        sa.Column("assessment_version", sa.String(100), nullable=False),
        *_common_columns(),
    )
    op.create_table(
        "processing_jobs",
        sa.Column(
            "content_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_items.id"),
            nullable=False,
        ),
        sa.Column("status", JOB_STATUS, nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True),
        sa.Column("error_category", sa.String(100)),
        sa.Column("error_message", sa.Text()),
        sa.Column("retry_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        *_common_columns(),
        sa.CheckConstraint("progress >= 0 AND progress <= 100", name="processing_progress_range"),
        sa.CheckConstraint(
            "attempts >= 0 AND max_attempts > 0 AND attempts <= max_attempts",
            name="processing_attempt_range",
        ),
    )
    op.create_table(
        "published_posts",
        sa.Column(
            "content_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_items.id"),
            nullable=False,
        ),
        sa.Column("platform", PLATFORM, nullable=False),
        sa.Column("platform_post_id", sa.String(255), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        *_common_columns(),
        sa.UniqueConstraint("platform", "platform_post_id", name="uq_platform_post"),
    )
    op.create_table(
        "publishing_jobs",
        sa.Column(
            "content_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_items.id"),
            nullable=False,
        ),
        sa.Column(
            "platform_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("platform_accounts.id"),
        ),
        sa.Column("status", JOB_STATUS, nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True)),
        sa.Column("error_category", sa.String(100)),
        sa.Column("error_message", sa.Text()),
        sa.Column("retry_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        *_common_columns(),
        sa.CheckConstraint("progress >= 0 AND progress <= 100", name="publishing_progress_range"),
        sa.CheckConstraint(
            "attempts >= 0 AND max_attempts > 0 AND attempts <= max_attempts",
            name="publishing_attempt_range",
        ),
    )
    op.create_table(
        "ranking_assessments",
        sa.Column(
            "content_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_items.id"),
            nullable=False,
        ),
        sa.Column(
            "platform_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("platform_accounts.id"),
        ),
        *[
            sa.Column(name, sa.Float())
            for name in (
                "engagement_score",
                "velocity_score",
                "freshness_score",
                "topic_score",
                "quality_score",
                "clipability_score",
                "retention_potential_score",
                "shareability_score",
                "rights_risk_score",
                "moderation_risk_score",
                "duplicate_risk_score",
                "platform_fit_score",
                "overall_score",
            )
        ],
        sa.Column("confidence", sa.Float()),
        sa.Column("reason", sa.Text()),
        sa.Column("evidence", JSONB),
        sa.Column("scoring_version", sa.String(100), nullable=False),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=False),
        *_common_columns(),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ranking_confidence_range",
        ),
    )
    op.create_table(
        "review_decisions",
        sa.Column(
            "content_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_items.id"),
            nullable=False,
        ),
        sa.Column(
            "reviewer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("outcome", REVIEW_OUTCOME, nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("approval_type", sa.String(100), nullable=False),
        *_common_columns(),
    )
    op.create_table(
        "rights_assessments",
        sa.Column(
            "content_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_items.id"),
            nullable=False,
        ),
        sa.Column("rights_state", RIGHTS_STATE, nullable=False),
        sa.Column("disposition", RIGHTS_DISPOSITION, nullable=False),
        sa.Column("is_automatic", sa.Boolean(), nullable=False),
        sa.Column("reviewer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("reviewer_notes", sa.Text()),
        sa.Column("evidence_reference", sa.String(2048)),
        sa.Column("policy_version", sa.String(100), nullable=False),
        sa.Column("assessment_version", sa.String(100), nullable=False),
        sa.Column("attribution_instructions", sa.Text()),
        sa.Column("usage_restrictions", sa.Text()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("geographic_restrictions", sa.Text()),
        sa.Column("platform_restrictions", sa.Text()),
        *_common_columns(),
    )
    op.create_table(
        "source_policies",
        sa.Column(
            "source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sources.id"), nullable=False
        ),
        sa.Column("approved", sa.Boolean(), nullable=False),
        sa.Column("policy_version", sa.String(100), nullable=False),
        sa.Column("restrictions", sa.Text()),
        *_common_columns(),
    )
    op.create_table(
        "user_roles",
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "role_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("roles.id"), nullable=False
        ),
        *_common_columns(),
        sa.UniqueConstraint("user_id", "role_id", name="uq_user_role"),
    )
    op.create_table(
        "performance_snapshots",
        sa.Column(
            "published_post_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("published_posts.id"),
            nullable=False,
        ),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("views", sa.Integer(), nullable=False),
        sa.Column("likes", sa.Integer(), nullable=False),
        sa.Column("comments", sa.Integer(), nullable=False),
        sa.Column("shares", sa.Integer(), nullable=False),
        *_common_columns(),
    )
    for table_name, columns in {
        "content_items": ("status", "created_at", "external_reference"),
        "audit_events": ("actor_id", "entity_type", "entity_id", "created_at", "correlation_id"),
        "clip_candidates": ("content_id",),
        "content_sources": ("content_id", "source_id"),
        "media_assets": ("content_id", "checksum"),
        "moderation_assessments": ("content_id",),
        "processing_jobs": ("content_id", "status"),
        "published_posts": ("content_id",),
        "publishing_jobs": ("content_id", "status"),
        "ranking_assessments": ("content_id",),
        "review_decisions": ("content_id", "reviewer_id"),
        "rights_assessments": ("content_id",),
        "source_policies": ("source_id",),
        "user_roles": ("user_id", "role_id"),
        "performance_snapshots": ("published_post_id",),
    }.items():
        for column in columns:
            op.create_index(f"ix_{table_name}_{column}", table_name, [column])


def downgrade() -> None:
    for table_name in (
        "performance_snapshots",
        "user_roles",
        "source_policies",
        "rights_assessments",
        "review_decisions",
        "ranking_assessments",
        "publishing_jobs",
        "published_posts",
        "processing_jobs",
        "moderation_assessments",
        "media_assets",
        "content_sources",
        "clip_candidates",
        "audit_events",
        "users",
        "sources",
        "roles",
        "platform_accounts",
        "content_items",
    ):
        op.drop_table(table_name)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for enum_type in reversed(ENUMS):
            enum_type.drop(bind, checkfirst=True)
