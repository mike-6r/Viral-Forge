"""Add durable Discord business-operations records.

Revision ID: 0023_discord_business_operations
Revises: 0022_discord_business_platform
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0023_discord_business_operations"
down_revision = "0022_discord_business_platform"
branch_labels = None
depends_on = None


def _uuid() -> sa.Uuid | postgresql.UUID:
    return postgresql.UUID(as_uuid=True) if op.get_bind().dialect.name == "postgresql" else sa.Uuid()


def _common() -> list[sa.Column[object]]:
    now = "now()" if op.get_bind().dialect.name == "postgresql" else "CURRENT_TIMESTAMP"
    return [
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text(now)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text(now)),
    ]


def upgrade() -> None:
    uuid = _uuid()
    op.add_column("discord_tickets", sa.Column("department", sa.String(80)))
    op.add_column("discord_tickets", sa.Column("tags", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    op.add_column("discord_tickets", sa.Column("escalation_reason", sa.Text()))
    for name in ("first_response_at", "last_customer_response_at", "last_staff_response_at", "sla_first_response_due_at", "sla_resolution_due_at", "resolved_at", "closed_at"):
        op.add_column("discord_tickets", sa.Column(name, sa.DateTime(timezone=True)))
    op.add_column("discord_tickets", sa.Column("satisfaction_score", sa.Integer()))
    if op.get_bind().dialect.name == "sqlite":
        # SQLite requires table recreation for a new foreign key. Batch mode keeps
        # the existing ticket rows intact while producing ORM-equivalent metadata.
        with op.batch_alter_table("discord_tickets") as batch:
            batch.add_column(sa.Column("customer_link_id", uuid))
            batch.create_foreign_key(
                "fk_discord_tickets_customer_link_id_discord_customer_links",
                "discord_customer_links",
                ["customer_link_id"],
                ["id"],
            )
    else:
        op.add_column(
            "discord_tickets",
            sa.Column(
                "customer_link_id",
                uuid,
                sa.ForeignKey(
                    "discord_customer_links.id",
                    name="fk_discord_tickets_customer_link_id_discord_customer_links",
                ),
            ),
        )
    op.create_index("ix_discord_tickets_sla_resolution_due_at", "discord_tickets", ["sla_resolution_due_at"])

    op.create_table("discord_ticket_notes", sa.Column("ticket_id", uuid, sa.ForeignKey("discord_tickets.id"), nullable=False), sa.Column("author_discord_user_id", sa.String(32), nullable=False), sa.Column("visibility", sa.String(30), nullable=False, server_default="STAFF_ONLY"), sa.Column("category", sa.String(50), nullable=False, server_default="NOTE"), sa.Column("content", sa.Text(), nullable=False), *_common())
    op.create_index("ix_discord_ticket_notes_ticket_id", "discord_ticket_notes", ["ticket_id"])
    op.create_table("discord_moderation_cases", sa.Column("guild_config_id", uuid, sa.ForeignKey("discord_guild_configs.id"), nullable=False), sa.Column("case_number", sa.Integer(), nullable=False), sa.Column("subject_discord_user_id", sa.String(32), nullable=False), sa.Column("moderator_discord_user_id", sa.String(32)), sa.Column("origin", sa.String(20), nullable=False, server_default="AUTOMATIC"), sa.Column("rule_key", sa.String(100), nullable=False), sa.Column("action", sa.String(30), nullable=False), sa.Column("status", sa.String(30), nullable=False, server_default="OPEN"), sa.Column("reason", sa.Text(), nullable=False), sa.Column("evidence_redacted", sa.JSON(), nullable=False, server_default=sa.text("'{}'")), sa.Column("duration_seconds", sa.Integer()), sa.Column("expires_at", sa.DateTime(timezone=True)), sa.Column("revoked_at", sa.DateTime(timezone=True)), sa.Column("appeal_status", sa.String(30)), sa.UniqueConstraint("guild_config_id", "case_number", name="uq_discord_moderation_case_number"), *_common())
    for name, column in (("ix_discord_moderation_cases_guild_config_id", "guild_config_id"), ("ix_discord_moderation_cases_subject_discord_user_id", "subject_discord_user_id"), ("ix_discord_moderation_cases_status", "status")):
        op.create_index(name, "discord_moderation_cases", [column])
    op.create_table("discord_appeals", sa.Column("moderation_case_id", uuid, sa.ForeignKey("discord_moderation_cases.id"), nullable=False), sa.Column("appellant_discord_user_id", sa.String(32), nullable=False), sa.Column("explanation", sa.Text(), nullable=False), sa.Column("requested_outcome", sa.String(100)), sa.Column("status", sa.String(30), nullable=False, server_default="OPEN"), sa.Column("reviewer_discord_user_id", sa.String(32)), sa.Column("decision_note", sa.Text()), sa.UniqueConstraint("moderation_case_id", name="uq_discord_appeal_case"), *_common())
    op.create_index("ix_discord_appeals_moderation_case_id", "discord_appeals", ["moderation_case_id"])
    op.create_index("ix_discord_appeals_appellant_discord_user_id", "discord_appeals", ["appellant_discord_user_id"])
    op.create_table("discord_role_grants", sa.Column("guild_config_id", uuid, sa.ForeignKey("discord_guild_configs.id"), nullable=False), sa.Column("discord_user_id", sa.String(32), nullable=False), sa.Column("role_key", sa.String(100), nullable=False), sa.Column("source", sa.String(50), nullable=False), sa.Column("assigned_by_discord_user_id", sa.String(32)), sa.Column("reason", sa.Text()), sa.Column("expires_at", sa.DateTime(timezone=True)), sa.Column("removed_at", sa.DateTime(timezone=True)), sa.Column("removal_reason", sa.Text()), sa.UniqueConstraint("guild_config_id", "discord_user_id", "role_key", "source", name="uq_discord_role_grant_source"), *_common())
    for name, column in (("ix_discord_role_grants_guild_config_id", "guild_config_id"), ("ix_discord_role_grants_discord_user_id", "discord_user_id"), ("ix_discord_role_grants_expires_at", "expires_at")):
        op.create_index(name, "discord_role_grants", [column])
    op.create_table("discord_role_sync_states", sa.Column("guild_config_id", uuid, sa.ForeignKey("discord_guild_configs.id"), nullable=False), sa.Column("discord_user_id", sa.String(32), nullable=False), sa.Column("status", sa.String(30), nullable=False, server_default="PENDING"), sa.Column("last_synced_at", sa.DateTime(timezone=True)), sa.Column("last_error", sa.Text()), sa.Column("details", sa.JSON(), nullable=False, server_default=sa.text("'{}'")), sa.UniqueConstraint("guild_config_id", "discord_user_id", name="uq_discord_role_sync_user"), *_common())
    op.create_index("ix_discord_role_sync_states_guild_config_id", "discord_role_sync_states", ["guild_config_id"])
    op.create_table("discord_staff_availability", sa.Column("guild_config_id", uuid, sa.ForeignKey("discord_guild_configs.id"), nullable=False), sa.Column("discord_user_id", sa.String(32), nullable=False), sa.Column("status", sa.String(30), nullable=False, server_default="AVAILABLE"), sa.Column("note", sa.String(300)), sa.UniqueConstraint("guild_config_id", "discord_user_id", name="uq_discord_staff_availability"), *_common())
    op.create_index("ix_discord_staff_availability_guild_config_id", "discord_staff_availability", ["guild_config_id"])
    op.create_table("discord_announcements", sa.Column("guild_config_id", uuid, sa.ForeignKey("discord_guild_configs.id"), nullable=False), sa.Column("author_discord_user_id", sa.String(32), nullable=False), sa.Column("title", sa.String(256), nullable=False), sa.Column("body", sa.Text(), nullable=False), sa.Column("target_channel_key", sa.String(100), nullable=False), sa.Column("notification_role_key", sa.String(100)), sa.Column("status", sa.String(30), nullable=False, server_default="DRAFT"), sa.Column("scheduled_for", sa.DateTime(timezone=True)), sa.Column("published_at", sa.DateTime(timezone=True)), sa.Column("discord_message_id", sa.String(32)), *_common())
    for name, column in (("ix_discord_announcements_guild_config_id", "guild_config_id"), ("ix_discord_announcements_status", "status"), ("ix_discord_announcements_scheduled_for", "scheduled_for")):
        op.create_index(name, "discord_announcements", [column])
    op.create_table("discord_incidents", sa.Column("guild_config_id", uuid, sa.ForeignKey("discord_guild_configs.id"), nullable=False), sa.Column("incident_number", sa.Integer(), nullable=False), sa.Column("title", sa.String(256), nullable=False), sa.Column("severity", sa.String(30), nullable=False), sa.Column("internal_status", sa.String(30), nullable=False, server_default="OPEN"), sa.Column("public_status", sa.String(30), nullable=False, server_default="INVESTIGATING"), sa.Column("owner_discord_user_id", sa.String(32)), sa.Column("affected_systems", sa.JSON(), nullable=False, server_default=sa.text("'[]'")), sa.Column("public_update", sa.Text()), sa.Column("internal_summary", sa.Text()), sa.Column("resolved_at", sa.DateTime(timezone=True)), sa.UniqueConstraint("guild_config_id", "incident_number", name="uq_discord_incident_number"), *_common())
    op.create_index("ix_discord_incidents_guild_config_id", "discord_incidents", ["guild_config_id"])
    op.create_table("discord_aggregate_snapshots", sa.Column("guild_config_id", uuid, sa.ForeignKey("discord_guild_configs.id"), nullable=False), sa.Column("snapshot_date", sa.String(10), nullable=False), sa.Column("metrics", sa.JSON(), nullable=False, server_default=sa.text("'{}'")), sa.UniqueConstraint("guild_config_id", "snapshot_date", name="uq_discord_snapshot_day"), *_common())
    op.create_index("ix_discord_aggregate_snapshots_guild_config_id", "discord_aggregate_snapshots", ["guild_config_id"])
    op.create_table("discord_staff_notes", sa.Column("guild_config_id", uuid, sa.ForeignKey("discord_guild_configs.id"), nullable=False), sa.Column("subject_type", sa.String(50), nullable=False), sa.Column("subject_id", sa.String(100), nullable=False), sa.Column("author_discord_user_id", sa.String(32), nullable=False), sa.Column("visibility", sa.String(30), nullable=False, server_default="STAFF_ONLY"), sa.Column("category", sa.String(50), nullable=False, server_default="NOTE"), sa.Column("content", sa.Text(), nullable=False), *_common())
    op.create_index("ix_discord_staff_notes_guild_config_id", "discord_staff_notes", ["guild_config_id"])
    op.create_index("ix_discord_staff_notes_subject_id", "discord_staff_notes", ["subject_id"])


def downgrade() -> None:
    for table in ("discord_staff_notes", "discord_aggregate_snapshots", "discord_incidents", "discord_announcements", "discord_staff_availability", "discord_role_sync_states", "discord_role_grants", "discord_appeals", "discord_moderation_cases", "discord_ticket_notes"):
        op.drop_table(table)
    op.drop_index("ix_discord_tickets_sla_resolution_due_at", table_name="discord_tickets")
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("discord_tickets") as batch:
            batch.drop_constraint(
                "fk_discord_tickets_customer_link_id_discord_customer_links", type_="foreignkey"
            )
            batch.drop_column("customer_link_id")
    else:
        op.drop_column("discord_tickets", "customer_link_id")
    for name in ("satisfaction_score", "closed_at", "resolved_at", "sla_resolution_due_at", "sla_first_response_due_at", "last_staff_response_at", "last_customer_response_at", "first_response_at", "escalation_reason", "tags", "department"):
        op.drop_column("discord_tickets", name)
