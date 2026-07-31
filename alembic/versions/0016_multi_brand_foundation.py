"""Add workspace/brand tenancy and backfill all existing operational records.

Revision ID: 0016_multi_brand_foundation
Revises: 0015_content_package
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0016_multi_brand_foundation"
down_revision = "0015_content_package"
branch_labels = None
depends_on = None

LEGACY_WORKSPACE_ID = "4e6768ac-d9bc-4eac-8f30-e73ffc510101"
LEGACY_BRAND_ID = "4e6768ac-d9bc-4eac-8f30-e73ffc510102"


def _common() -> list[sa.Column[object]]:
    timestamp = "now()" if op.get_bind().dialect.name == "postgresql" else "CURRENT_TIMESTAMP"
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text(timestamp)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text(timestamp)),
    ]


def _json() -> sa.JSON:
    return sa.JSON()


def _brand_column(nullable: bool = True) -> sa.Column[object]:
    return sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id"), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False, unique=True),
        sa.Column("timezone", sa.String(100), nullable=False),
        sa.Column("is_legacy", sa.Boolean(), nullable=False),
        *_common(),
    )
    op.create_table(
        "brands",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_legacy", sa.Boolean(), nullable=False),
        *_common(),
        sa.UniqueConstraint("workspace_id", "slug", name="uq_brand_workspace_slug"),
    )
    op.create_table(
        "brand_memberships",
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(50), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        *_common(),
        sa.UniqueConstraint("brand_id", "user_id", name="uq_brand_member"),
    )
    op.create_table(
        "content_profiles",
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id"), nullable=False),
        sa.Column("niche_name", sa.String(255), nullable=False),
        sa.Column("discovery_categories", _json(), nullable=False), sa.Column("included_keywords", _json(), nullable=False), sa.Column("excluded_keywords", _json(), nullable=False), sa.Column("preferred_source_providers", _json(), nullable=False),
        sa.Column("min_clip_duration_seconds", sa.Integer(), nullable=False), sa.Column("max_clip_duration_seconds", sa.Integer(), nullable=False), sa.Column("opportunity_weights_json", _json(), nullable=False), sa.Column("opportunity_profile_reference", sa.String(255)),
        sa.Column("caption_tone", sa.String(255), nullable=False), sa.Column("title_style", sa.String(255), nullable=False), sa.Column("hashtag_rules", _json(), nullable=False), sa.Column("branding_behavior", _json(), nullable=False), sa.Column("review_requirements", _json(), nullable=False),
        sa.Column("maximum_posts_per_day", sa.Integer(), nullable=False), sa.Column("target_platforms", _json(), nullable=False), sa.Column("language", sa.String(50), nullable=False), sa.Column("timezone", sa.String(100), nullable=False), *_common(), sa.UniqueConstraint("brand_id", name="uq_content_profile_brand"),
    )
    for table, account in (("source_accounts", False), ("destination_accounts", True)):
        columns: list[sa.Column[object]] = [
            sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id"), nullable=False), sa.Column("provider", sa.String(50), nullable=False), sa.Column("account_reference", sa.String(500), nullable=False),
            sa.Column("credential_reference_id", sa.String(500)) if account else sa.Column("public_url", sa.String(2048)), sa.Column("display_name", sa.String(500)), sa.Column("provider_metadata", _json(), nullable=False), sa.Column("is_active", sa.Boolean(), nullable=False), *_common(), sa.UniqueConstraint("brand_id", "provider", "account_reference", name=f"uq_{table[:-1]}_brand_provider"),
        ]
        op.create_table(table, *columns)
    op.create_table("branding_profiles", sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id"), nullable=False), sa.Column("display_name", sa.String(255), nullable=False), sa.Column("attribution_template", sa.Text()), sa.Column("behavior_json", _json(), nullable=False), *_common(), sa.UniqueConstraint("brand_id", name="uq_branding_profile_brand"))
    op.create_table("review_policies", sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id"), nullable=False), sa.Column("requirements_json", _json(), nullable=False), sa.Column("required_review_count", sa.Integer(), nullable=False), *_common(), sa.UniqueConstraint("brand_id", name="uq_review_policy_brand"))
    op.create_table("posting_policies", sa.Column("brand_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("brands.id"), nullable=False), sa.Column("maximum_posts_per_day", sa.Integer(), nullable=False), sa.Column("target_platforms", _json(), nullable=False), sa.Column("timezone", sa.String(100), nullable=False), sa.Column("policy_json", _json(), nullable=False), *_common(), sa.UniqueConstraint("brand_id", name="uq_posting_policy_brand"))
    op.execute(sa.text(f"INSERT INTO workspaces (id, name, slug, timezone, is_legacy) VALUES ('{LEGACY_WORKSPACE_ID}', 'Legacy Workspace', 'legacy', 'UTC', true)"))
    op.execute(sa.text(f"INSERT INTO brands (id, workspace_id, name, slug, is_active, is_legacy) VALUES ('{LEGACY_BRAND_ID}', '{LEGACY_WORKSPACE_ID}', 'Legacy Brand', 'legacy', true, true)"))
    op.execute(sa.text(f"INSERT INTO content_profiles (id, brand_id, niche_name, discovery_categories, included_keywords, excluded_keywords, preferred_source_providers, min_clip_duration_seconds, max_clip_duration_seconds, opportunity_weights_json, caption_tone, title_style, hashtag_rules, branding_behavior, review_requirements, maximum_posts_per_day, target_platforms, language, timezone) VALUES ('4e6768ac-d9bc-4eac-8f30-e73ffc510103', '{LEGACY_BRAND_ID}', 'legacy', '[]', '[]', '[]', '[]', 15, 60, '{{}}', 'neutral', 'factual', '{{}}', '{{}}', '{{}}', 0, '[]', 'und', 'UTC')"))
    membership_id = "md5(id::text)::uuid" if op.get_bind().dialect.name == "postgresql" else "lower(hex(randomblob(16)))"
    op.execute(sa.text(f"INSERT INTO brand_memberships (id, brand_id, user_id, role, is_default) SELECT {membership_id}, '{LEGACY_BRAND_ID}', id, 'ADMIN', true FROM users"))
    tables = ("discovery_sources", "discovered_media", "discovery_runs", "production_projects", "production_sources", "video_analyses", "opportunity_generation_runs", "clip_opportunities", "production_clips", "content_packages", "posting_queue_items", "audit_events")
    for table in tables:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("brand_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.execute(sa.text(f"UPDATE {table} SET brand_id = '{LEGACY_BRAND_ID}' WHERE brand_id IS NULL"))
        with op.batch_alter_table(table) as batch:
            batch.alter_column("brand_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)
            batch.create_foreign_key(f"fk_{table}_brand_id", "brands", ["brand_id"], ["id"])
            batch.create_index(f"ix_{table}_brand_id", ["brand_id"])


def downgrade() -> None:
    tables = ("audit_events", "posting_queue_items", "content_packages", "production_clips", "clip_opportunities", "opportunity_generation_runs", "video_analyses", "production_sources", "production_projects", "discovery_runs", "discovered_media", "discovery_sources")
    for table in tables:
        with op.batch_alter_table(table) as batch:
            batch.drop_index(f"ix_{table}_brand_id")
            batch.drop_constraint(f"fk_{table}_brand_id", type_="foreignkey")
            batch.drop_column("brand_id")
    for table in ("posting_policies", "review_policies", "branding_profiles", "destination_accounts", "source_accounts", "content_profiles", "brand_memberships", "brands", "workspaces"):
        op.drop_table(table)
