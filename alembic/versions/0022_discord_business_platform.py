"""Add durable configuration and privacy boundaries for the Discord business platform.

Revision ID: 0022_discord_business_platform
Revises: 0021_media_preview
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0022_discord_business_platform"
down_revision = "0021_media_preview"
branch_labels = None
depends_on = None


def _uuid():
    return postgresql.UUID(as_uuid=True) if op.get_bind().dialect.name == "postgresql" else sa.Uuid()


def _common():
    now = "now()" if op.get_bind().dialect.name == "postgresql" else "CURRENT_TIMESTAMP"
    return [sa.Column("id", _uuid(), primary_key=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text(now)), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text(now))]


def upgrade() -> None:
    uuid = _uuid()
    op.create_table("discord_guild_configs", sa.Column("guild_id", sa.String(32), nullable=False, unique=True), sa.Column("guild_name", sa.String(255)), sa.Column("config_version", sa.String(50), nullable=False), sa.Column("setup_state", sa.String(50), nullable=False, server_default="PREVIEW"), sa.Column("setup_revision", sa.Integer(), nullable=False, server_default="0"), sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()), *_common())
    op.create_table("discord_guild_resources", sa.Column("guild_config_id", uuid, sa.ForeignKey("discord_guild_configs.id"), nullable=False), sa.Column("resource_type", sa.String(30), nullable=False), sa.Column("resource_key", sa.String(100), nullable=False), sa.Column("discord_id", sa.String(32), nullable=False), sa.Column("revision", sa.Integer(), nullable=False, server_default="1"), sa.UniqueConstraint("guild_config_id", "resource_type", "resource_key", name="uq_discord_resource_key"), *_common())
    op.create_index("ix_discord_guild_resources_guild_config_id", "discord_guild_resources", ["guild_config_id"])
    op.create_table("discord_rules_acceptances", sa.Column("guild_config_id", uuid, sa.ForeignKey("discord_guild_configs.id"), nullable=False), sa.Column("discord_user_id", sa.String(32), nullable=False), sa.Column("rules_version", sa.String(50), nullable=False), sa.UniqueConstraint("guild_config_id", "discord_user_id", name="uq_discord_rules_user"), *_common())
    op.create_index("ix_discord_rules_acceptances_guild_config_id", "discord_rules_acceptances", ["guild_config_id"])
    op.create_table("discord_tickets", sa.Column("guild_config_id", uuid, sa.ForeignKey("discord_guild_configs.id"), nullable=False), sa.Column("ticket_number", sa.Integer(), nullable=False), sa.Column("discord_channel_id", sa.String(32), nullable=False), sa.Column("requester_discord_user_id", sa.String(32), nullable=False), sa.Column("ticket_type", sa.String(50), nullable=False), sa.Column("priority", sa.String(20), nullable=False, server_default="normal"), sa.Column("status", sa.String(30), nullable=False, server_default="OPEN"), sa.Column("assigned_staff_discord_user_id", sa.String(32)), sa.Column("notes", sa.Text()), sa.UniqueConstraint("guild_config_id", "ticket_number", name="uq_discord_ticket_number"), sa.UniqueConstraint("discord_channel_id", name="uq_discord_ticket_channel"), *_common())
    for name, column in (("ix_discord_tickets_guild_config_id", "guild_config_id"), ("ix_discord_tickets_requester_discord_user_id", "requester_discord_user_id"), ("ix_discord_tickets_status", "status")):
        op.create_index(name, "discord_tickets", [column])
    op.create_table("discord_onboarding_progress", sa.Column("guild_config_id", uuid, sa.ForeignKey("discord_guild_configs.id"), nullable=False), sa.Column("discord_user_id", sa.String(32), nullable=False), sa.Column("account_type", sa.String(50)), sa.Column("primary_goal", sa.String(100)), sa.Column("status", sa.String(30), nullable=False, server_default="STARTED"), sa.UniqueConstraint("guild_config_id", "discord_user_id", name="uq_discord_onboarding_user"), *_common())
    op.create_index("ix_discord_onboarding_progress_guild_config_id", "discord_onboarding_progress", ["guild_config_id"])
    op.create_table("discord_customer_links", sa.Column("guild_config_id", uuid, sa.ForeignKey("discord_guild_configs.id"), nullable=False), sa.Column("discord_user_id", sa.String(32), nullable=False), sa.Column("workspace_id", uuid, sa.ForeignKey("workspaces.id")), sa.Column("brand_id", uuid, sa.ForeignKey("brands.id")), sa.Column("relationship", sa.String(50), nullable=False, server_default="CUSTOMER"), sa.UniqueConstraint("guild_config_id", "discord_user_id", name="uq_discord_customer_user"), *_common())
    op.create_index("ix_discord_customer_links_guild_config_id", "discord_customer_links", ["guild_config_id"])
    op.create_table("discord_published_embeds", sa.Column("guild_config_id", uuid, sa.ForeignKey("discord_guild_configs.id"), nullable=False), sa.Column("embed_key", sa.String(100), nullable=False), sa.Column("channel_resource_key", sa.String(100), nullable=False), sa.Column("discord_message_id", sa.String(32), nullable=False), sa.Column("config_version", sa.String(50), nullable=False), sa.UniqueConstraint("guild_config_id", "embed_key", name="uq_discord_published_embed"), *_common())
    op.create_index("ix_discord_published_embeds_guild_config_id", "discord_published_embeds", ["guild_config_id"])


def downgrade() -> None:
    op.drop_table("discord_published_embeds")
    op.drop_table("discord_customer_links")
    op.drop_table("discord_onboarding_progress")
    op.drop_table("discord_tickets")
    op.drop_table("discord_rules_acceptances")
    op.drop_table("discord_guild_resources")
    op.drop_table("discord_guild_configs")
