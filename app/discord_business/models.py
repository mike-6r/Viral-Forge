"""Persisted Discord configuration; no token, OAuth secret, or raw credential is stored here."""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base
from app.common.types import UUIDTimestampMixin


class DiscordGuildConfig(UUIDTimestampMixin, Base):
    __tablename__ = "discord_guild_configs"

    guild_id: Mapped[str] = mapped_column(String(32), unique=True)
    guild_name: Mapped[str | None] = mapped_column(String(255))
    config_version: Mapped[str] = mapped_column(String(50), nullable=False)
    setup_state: Mapped[str] = mapped_column(String(50), default="PREVIEW")
    setup_revision: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class DiscordGuildResource(UUIDTimestampMixin, Base):
    __tablename__ = "discord_guild_resources"
    __table_args__ = (
        UniqueConstraint(
            "guild_config_id", "resource_type", "resource_key", name="uq_discord_resource_key"
        ),
    )

    guild_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("discord_guild_configs.id"), index=True
    )
    resource_type: Mapped[str] = mapped_column(String(30))
    resource_key: Mapped[str] = mapped_column(String(100))
    discord_id: Mapped[str] = mapped_column(String(32))
    revision: Mapped[int] = mapped_column(Integer, default=1)


class DiscordRulesAcceptance(UUIDTimestampMixin, Base):
    __tablename__ = "discord_rules_acceptances"
    __table_args__ = (
        UniqueConstraint("guild_config_id", "discord_user_id", name="uq_discord_rules_user"),
    )

    guild_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("discord_guild_configs.id"), index=True
    )
    discord_user_id: Mapped[str] = mapped_column(String(32))
    rules_version: Mapped[str] = mapped_column(String(50))


class DiscordTicket(UUIDTimestampMixin, Base):
    __tablename__ = "discord_tickets"
    __table_args__ = (
        UniqueConstraint("guild_config_id", "ticket_number", name="uq_discord_ticket_number"),
        UniqueConstraint("discord_channel_id", name="uq_discord_ticket_channel"),
    )

    guild_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("discord_guild_configs.id"), index=True
    )
    ticket_number: Mapped[int] = mapped_column(Integer)
    discord_channel_id: Mapped[str] = mapped_column(String(32))
    requester_discord_user_id: Mapped[str] = mapped_column(String(32), index=True)
    ticket_type: Mapped[str] = mapped_column(String(50))
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    status: Mapped[str] = mapped_column(String(30), default="OPEN", index=True)
    assigned_staff_discord_user_id: Mapped[str | None] = mapped_column(String(32))
    notes: Mapped[str | None] = mapped_column(Text)
    department: Mapped[str | None] = mapped_column(String(80))
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    escalation_reason: Mapped[str | None] = mapped_column(Text)
    first_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_customer_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_staff_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sla_first_response_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sla_resolution_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    satisfaction_score: Mapped[int | None] = mapped_column(Integer)
    customer_link_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("discord_customer_links.id")
    )


class DiscordTicketNote(UUIDTimestampMixin, Base):
    __tablename__ = "discord_ticket_notes"

    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("discord_tickets.id"), index=True
    )
    author_discord_user_id: Mapped[str] = mapped_column(String(32))
    visibility: Mapped[str] = mapped_column(String(30), default="STAFF_ONLY")
    category: Mapped[str] = mapped_column(String(50), default="NOTE")
    content: Mapped[str] = mapped_column(Text)


class DiscordModerationCase(UUIDTimestampMixin, Base):
    __tablename__ = "discord_moderation_cases"
    __table_args__ = (
        UniqueConstraint("guild_config_id", "case_number", name="uq_discord_moderation_case_number"),
    )

    guild_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("discord_guild_configs.id"), index=True
    )
    case_number: Mapped[int] = mapped_column(Integer)
    subject_discord_user_id: Mapped[str] = mapped_column(String(32), index=True)
    moderator_discord_user_id: Mapped[str | None] = mapped_column(String(32))
    origin: Mapped[str] = mapped_column(String(20), default="AUTOMATIC")
    rule_key: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), default="OPEN", index=True)
    reason: Mapped[str] = mapped_column(Text)
    evidence_redacted: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    appeal_status: Mapped[str | None] = mapped_column(String(30))


class DiscordAppeal(UUIDTimestampMixin, Base):
    __tablename__ = "discord_appeals"
    __table_args__ = (UniqueConstraint("moderation_case_id", name="uq_discord_appeal_case"),)

    moderation_case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("discord_moderation_cases.id"), index=True
    )
    appellant_discord_user_id: Mapped[str] = mapped_column(String(32), index=True)
    explanation: Mapped[str] = mapped_column(Text)
    requested_outcome: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default="OPEN")
    reviewer_discord_user_id: Mapped[str | None] = mapped_column(String(32))
    decision_note: Mapped[str | None] = mapped_column(Text)


class DiscordRoleGrant(UUIDTimestampMixin, Base):
    __tablename__ = "discord_role_grants"
    __table_args__ = (
        UniqueConstraint(
            "guild_config_id", "discord_user_id", "role_key", "source", name="uq_discord_role_grant_source"
        ),
    )

    guild_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("discord_guild_configs.id"), index=True
    )
    discord_user_id: Mapped[str] = mapped_column(String(32), index=True)
    role_key: Mapped[str] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(50))
    assigned_by_discord_user_id: Mapped[str | None] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removal_reason: Mapped[str | None] = mapped_column(Text)


class DiscordRoleSyncState(UUIDTimestampMixin, Base):
    __tablename__ = "discord_role_sync_states"
    __table_args__ = (
        UniqueConstraint("guild_config_id", "discord_user_id", name="uq_discord_role_sync_user"),
    )

    guild_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("discord_guild_configs.id"), index=True
    )
    discord_user_id: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(30), default="PENDING")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)


class DiscordStaffAvailability(UUIDTimestampMixin, Base):
    __tablename__ = "discord_staff_availability"
    __table_args__ = (
        UniqueConstraint("guild_config_id", "discord_user_id", name="uq_discord_staff_availability"),
    )

    guild_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("discord_guild_configs.id"), index=True
    )
    discord_user_id: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(30), default="AVAILABLE")
    note: Mapped[str | None] = mapped_column(String(300))


class DiscordAnnouncement(UUIDTimestampMixin, Base):
    __tablename__ = "discord_announcements"

    guild_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("discord_guild_configs.id"), index=True
    )
    author_discord_user_id: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(256))
    body: Mapped[str] = mapped_column(Text)
    target_channel_key: Mapped[str] = mapped_column(String(100))
    notification_role_key: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default="DRAFT", index=True)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discord_message_id: Mapped[str | None] = mapped_column(String(32))


class DiscordIncident(UUIDTimestampMixin, Base):
    __tablename__ = "discord_incidents"
    __table_args__ = (
        UniqueConstraint("guild_config_id", "incident_number", name="uq_discord_incident_number"),
    )

    guild_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("discord_guild_configs.id"), index=True
    )
    incident_number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(256))
    severity: Mapped[str] = mapped_column(String(30))
    internal_status: Mapped[str] = mapped_column(String(30), default="OPEN")
    public_status: Mapped[str] = mapped_column(String(30), default="INVESTIGATING")
    owner_discord_user_id: Mapped[str | None] = mapped_column(String(32))
    affected_systems: Mapped[list[str]] = mapped_column(JSON, default=list)
    public_update: Mapped[str | None] = mapped_column(Text)
    internal_summary: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DiscordAggregateSnapshot(UUIDTimestampMixin, Base):
    __tablename__ = "discord_aggregate_snapshots"
    __table_args__ = (UniqueConstraint("guild_config_id", "snapshot_date", name="uq_discord_snapshot_day"),)

    guild_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("discord_guild_configs.id"), index=True
    )
    snapshot_date: Mapped[str] = mapped_column(String(10))
    metrics: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)


class DiscordStaffNote(UUIDTimestampMixin, Base):
    __tablename__ = "discord_staff_notes"

    guild_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("discord_guild_configs.id"), index=True
    )
    subject_type: Mapped[str] = mapped_column(String(50))
    subject_id: Mapped[str] = mapped_column(String(100), index=True)
    author_discord_user_id: Mapped[str] = mapped_column(String(32))
    visibility: Mapped[str] = mapped_column(String(30), default="STAFF_ONLY")
    category: Mapped[str] = mapped_column(String(50), default="NOTE")
    content: Mapped[str] = mapped_column(Text)


class DiscordOnboardingProgress(UUIDTimestampMixin, Base):
    __tablename__ = "discord_onboarding_progress"
    __table_args__ = (
        UniqueConstraint("guild_config_id", "discord_user_id", name="uq_discord_onboarding_user"),
    )

    guild_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("discord_guild_configs.id"), index=True
    )
    discord_user_id: Mapped[str] = mapped_column(String(32))
    account_type: Mapped[str | None] = mapped_column(String(50))
    primary_goal: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default="STARTED")


class DiscordCustomerLink(UUIDTimestampMixin, Base):
    __tablename__ = "discord_customer_links"
    __table_args__ = (
        UniqueConstraint("guild_config_id", "discord_user_id", name="uq_discord_customer_user"),
    )

    guild_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("discord_guild_configs.id"), index=True
    )
    discord_user_id: Mapped[str] = mapped_column(String(32))
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id")
    )
    brand_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"))
    relationship: Mapped[str] = mapped_column(String(50), default="CUSTOMER")


class DiscordPublishedEmbed(UUIDTimestampMixin, Base):
    __tablename__ = "discord_published_embeds"
    __table_args__ = (
        UniqueConstraint("guild_config_id", "embed_key", name="uq_discord_published_embed"),
    )

    guild_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("discord_guild_configs.id"), index=True
    )
    embed_key: Mapped[str] = mapped_column(String(100))
    channel_resource_key: Mapped[str] = mapped_column(String(100))
    discord_message_id: Mapped[str] = mapped_column(String(32))
    config_version: Mapped[str] = mapped_column(String(50))
