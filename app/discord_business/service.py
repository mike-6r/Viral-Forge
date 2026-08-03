"""Planning, persistence, and safe Discord resource repair helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.discord_business.models import (
    DiscordCustomerLink,
    DiscordGuildConfig,
    DiscordGuildResource,
    DiscordOnboardingProgress,
    DiscordPublishedEmbed,
    DiscordRulesAcceptance,
    DiscordTicket,
)

CONFIG_ROOT = Path("config/discord")


class DiscordBusinessError(Exception):
    pass


@dataclass(frozen=True)
class ResourcePlan:
    resource_type: str
    resource_key: str
    name: str
    audience: str
    category_key: str | None = None
    read_only: bool = False
    kind: str = "text"
    tags: tuple[str, ...] = ()


def load_config(root: Path = CONFIG_ROOT) -> dict[str, Any]:
    required = (
        "server",
        "roles",
        "channels",
        "permissions",
        "embeds",
        "buttons",
        "tickets",
        "onboarding",
        "plans",
        "branding",
        "automod",
        "role_panels",
        "role_sync",
        "ticket_sla",
        "ticket_departments",
        "support_macros",
        "incidents",
        "announcements",
        "dashboards",
    )
    loaded: dict[str, Any] = {}
    for name in required:
        path = root / f"{name}.yml"
        if not path.is_file():
            raise DiscordBusinessError(f"missing Discord configuration: {path}")
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise DiscordBusinessError(f"invalid Discord configuration: {path}")
        loaded[name] = value
    return loaded


def plan_resources(config: dict[str, Any]) -> list[ResourcePlan]:
    result: list[ResourcePlan] = []
    for item in config["roles"].get("roles", []):
        result.append(ResourcePlan("role", item["key"], item["name"], item["audience"]))
    for item in config["server"].get("categories", []):
        result.append(ResourcePlan("category", item["key"], item["name"], item["audience"]))
    for item in config["channels"].get("channels", []):
        result.append(
            ResourcePlan(
                "channel",
                item["key"],
                item["name"],
                item["audience"],
                item["category"],
                bool(item.get("read_only")),
                str(item.get("kind", "text")),
                tuple(str(tag) for tag in item.get("tags", [])),
            )
        )
    return result


class BusinessRepository:
    """All writes are idempotent and retain only Discord identifiers and non-secret metadata."""

    def guild_config(
        self, session: Session, guild_id: int, guild_name: str, version: str
    ) -> DiscordGuildConfig:
        row = session.scalar(
            select(DiscordGuildConfig).where(DiscordGuildConfig.guild_id == str(guild_id))
        )
        if row is None:
            row = DiscordGuildConfig(
                guild_id=str(guild_id), guild_name=guild_name, config_version=version
            )
            session.add(row)
            session.flush()
        else:
            row.guild_name, row.config_version = guild_name, version
        return row

    def resource_id(
        self, session: Session, guild_id: int, resource_type: str, key: str
    ) -> int | None:
        config = session.scalar(
            select(DiscordGuildConfig).where(DiscordGuildConfig.guild_id == str(guild_id))
        )
        if config is None:
            return None
        row = session.scalar(
            select(DiscordGuildResource).where(
                DiscordGuildResource.guild_config_id == config.id,
                DiscordGuildResource.resource_type == resource_type,
                DiscordGuildResource.resource_key == key,
            )
        )
        return int(row.discord_id) if row else None

    def save_resource(
        self, session: Session, config: DiscordGuildConfig, item: ResourcePlan, discord_id: int
    ) -> None:
        row = session.scalar(
            select(DiscordGuildResource).where(
                DiscordGuildResource.guild_config_id == config.id,
                DiscordGuildResource.resource_type == item.resource_type,
                DiscordGuildResource.resource_key == item.resource_key,
            )
        )
        if row is None:
            session.add(
                DiscordGuildResource(
                    guild_config_id=config.id,
                    resource_type=item.resource_type,
                    resource_key=item.resource_key,
                    discord_id=str(discord_id),
                )
            )
        else:
            row.discord_id, row.revision = str(discord_id), row.revision + 1

    def accept_rules(
        self, session: Session, config: DiscordGuildConfig, discord_user_id: int
    ) -> bool:
        row = session.scalar(
            select(DiscordRulesAcceptance).where(
                DiscordRulesAcceptance.guild_config_id == config.id,
                DiscordRulesAcceptance.discord_user_id == str(discord_user_id),
            )
        )
        if row:
            row.rules_version = config.config_version
            return False
        session.add(
            DiscordRulesAcceptance(
                guild_config_id=config.id,
                discord_user_id=str(discord_user_id),
                rules_version=config.config_version,
            )
        )
        return True

    def save_onboarding(
        self,
        session: Session,
        config: DiscordGuildConfig,
        user_id: int,
        account_type: str | None,
        goal: str | None,
    ) -> DiscordOnboardingProgress:
        row = session.scalar(
            select(DiscordOnboardingProgress).where(
                DiscordOnboardingProgress.guild_config_id == config.id,
                DiscordOnboardingProgress.discord_user_id == str(user_id),
            )
        )
        if row is None:
            row = DiscordOnboardingProgress(guild_config_id=config.id, discord_user_id=str(user_id))
            session.add(row)
        row.account_type, row.primary_goal, row.status = (
            account_type,
            goal,
            "COMPLETED" if account_type and goal else "STARTED",
        )
        return row

    def open_ticket(
        self,
        session: Session,
        config: DiscordGuildConfig,
        channel_id: int,
        user_id: int,
        ticket_type: str,
        priority: str,
    ) -> DiscordTicket:
        existing = session.scalar(
            select(DiscordTicket).where(DiscordTicket.discord_channel_id == str(channel_id))
        )
        if existing:
            return existing
        number = (
            session.scalar(
                select(func.max(DiscordTicket.ticket_number)).where(
                    DiscordTicket.guild_config_id == config.id
                )
            )
            or 0
        ) + 1
        row = DiscordTicket(
            guild_config_id=config.id,
            ticket_number=number,
            discord_channel_id=str(channel_id),
            requester_discord_user_id=str(user_id),
            ticket_type=ticket_type,
            priority=priority,
        )
        session.add(row)
        return row

    def tickets_for_user(
        self, session: Session, config: DiscordGuildConfig, user_id: int
    ) -> list[DiscordTicket]:
        return list(
            session.scalars(
                select(DiscordTicket)
                .where(
                    DiscordTicket.guild_config_id == config.id,
                    DiscordTicket.requester_discord_user_id == str(user_id),
                )
                .order_by(DiscordTicket.created_at.desc())
            )
        )

    def customer_link(
        self, session: Session, config: DiscordGuildConfig, user_id: int
    ) -> DiscordCustomerLink | None:
        return session.scalar(
            select(DiscordCustomerLink).where(
                DiscordCustomerLink.guild_config_id == config.id,
                DiscordCustomerLink.discord_user_id == str(user_id),
            )
        )

    def save_embed(
        self,
        session: Session,
        config: DiscordGuildConfig,
        embed_key: str,
        channel_key: str,
        message_id: int,
    ) -> None:
        row = session.scalar(
            select(DiscordPublishedEmbed).where(
                DiscordPublishedEmbed.guild_config_id == config.id,
                DiscordPublishedEmbed.embed_key == embed_key,
            )
        )
        if row is None:
            session.add(
                DiscordPublishedEmbed(
                    guild_config_id=config.id,
                    embed_key=embed_key,
                    channel_resource_key=channel_key,
                    discord_message_id=str(message_id),
                    config_version=config.config_version,
                )
            )
        else:
            row.channel_resource_key, row.discord_message_id, row.config_version = (
                channel_key,
                str(message_id),
                config.config_version,
            )

    def published_embed(
        self, session: Session, config: DiscordGuildConfig, embed_key: str
    ) -> DiscordPublishedEmbed | None:
        return session.scalar(
            select(DiscordPublishedEmbed).where(
                DiscordPublishedEmbed.guild_config_id == config.id,
                DiscordPublishedEmbed.embed_key == embed_key,
            )
        )


def audience_role_keys(audience: str) -> set[str]:
    return {
        "public": set(),
        "member": {"member"},
        "customer": {"customer"},
        "staff": {
            "owner",
            "administrator",
            "operations_lead",
            "content_operator",
            "customer_success",
            "support_team",
            "developer",
        },
    }.get(audience, set())
