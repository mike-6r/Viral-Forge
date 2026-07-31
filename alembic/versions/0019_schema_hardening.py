"""Reconcile released metadata with the live schema without altering released revisions.

Revision ID: 0019_schema_hardening
Revises: 0018_analytics_feedback
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0019_schema_hardening"
down_revision = "0018_analytics_feedback"
branch_labels = None
depends_on = None

INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ix_brand_memberships_brand_id", "brand_memberships", ("brand_id",)),
    ("ix_brand_memberships_is_default", "brand_memberships", ("is_default",)),
    ("ix_brand_memberships_user_id", "brand_memberships", ("user_id",)),
    ("ix_branding_profiles_brand_id", "branding_profiles", ("brand_id",)),
    ("ix_brands_is_active", "brands", ("is_active",)),
    ("ix_brands_workspace_id", "brands", ("workspace_id",)),
    ("ix_content_profiles_brand_id", "content_profiles", ("brand_id",)),
    ("ix_destination_accounts_brand_id", "destination_accounts", ("brand_id",)),
    ("ix_posting_policies_brand_id", "posting_policies", ("brand_id",)),
    ("ix_production_projects_created_actor_id", "production_projects", ("created_actor_id",)),
    ("ix_review_policies_brand_id", "review_policies", ("brand_id",)),
    ("ix_source_accounts_brand_id", "source_accounts", ("brand_id",)),
)


def _active_job_ids_are_valid() -> bool:
    invalid = op.get_bind().execute(
        sa.text(
            "SELECT COUNT(*) FROM feed_subscriptions "
            "WHERE active_job_id IS NOT NULL "
            "AND active_job_id !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'"
        )
    ).scalar_one()
    return int(invalid) == 0


def upgrade() -> None:
    bind = op.get_bind()
    for name, table, columns in INDEXES:
        op.create_index(name, table, list(columns))
    if bind.dialect.name == "postgresql":
        if not _active_job_ids_are_valid():
            raise RuntimeError("feed_subscriptions.active_job_id contains a non-UUID value")
        op.alter_column(
            "feed_subscriptions",
            "active_job_id",
            existing_type=sa.String(length=36),
            type_=postgresql.UUID(as_uuid=True),
            postgresql_using="NULLIF(active_job_id, '')::uuid",
        )
        op.alter_column(
            "ingestion_jobs",
            "result_metadata",
            existing_type=sa.JSON(),
            type_=postgresql.JSONB(astext_type=sa.Text()),
            postgresql_using="result_metadata::jsonb",
        )
    else:
        # SQLite has no native UUID/JSONB types. Recreate deterministically with
        # SQLAlchemy's portable UUID representation; existing NULL values survive.
        with op.batch_alter_table("feed_subscriptions", recreate="always") as batch:
            batch.alter_column("active_job_id", existing_type=sa.String(length=36), type_=sa.Uuid())
        # Revision 0010 could not add this cyclic FK on SQLite. Recreate the
        # table now that both tables exist so disposable SQLite matches ORM.
        with op.batch_alter_table("production_projects", recreate="always") as batch:
            batch.create_foreign_key(
                "fk_projects_selected_source",
                "production_sources",
                ["selected_source_id"],
                ["id"],
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.alter_column(
            "ingestion_jobs",
            "result_metadata",
            existing_type=postgresql.JSONB(astext_type=sa.Text()),
            type_=sa.JSON(),
            postgresql_using="result_metadata::json",
        )
        op.alter_column(
            "feed_subscriptions",
            "active_job_id",
            existing_type=postgresql.UUID(as_uuid=True),
            type_=sa.String(length=36),
            postgresql_using="active_job_id::text",
        )
    else:
        with op.batch_alter_table("feed_subscriptions", recreate="always") as batch:
            batch.alter_column("active_job_id", existing_type=sa.Uuid(), type_=sa.String(length=36))
        with op.batch_alter_table("production_projects", recreate="always") as batch:
            batch.drop_constraint("fk_projects_selected_source", type_="foreignkey")
    for name, table, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table)
