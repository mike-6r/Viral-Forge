"""Persisted Discord configuration; no token, OAuth secret, or raw credential is stored here."""

import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
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
