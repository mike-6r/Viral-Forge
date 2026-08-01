"""Discord-facing business platform. Public flows never invoke production controls."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from sqlalchemy.orm import Session

from app.common.db import get_session
from app.discord_business.models import (
    DiscordGuildConfig,
    DiscordGuildResource,
    DiscordPublishedEmbed,
)
from app.discord_business.service import (
    BusinessRepository,
    DiscordBusinessError,
    ResourcePlan,
    audience_role_keys,
    load_config,
    plan_resources,
)

BUSINESS_ASSET_ROOT = Path("assets/discord/viralforge")
BUSINESS_COLOR = 0xFF4D44
STAFF_ROLE_KEYS = {
    "owner",
    "administrator",
    "operations_lead",
    "content_operator",
    "customer_success",
    "support_team",
    "developer",
}

ACTION_LABELS = {
    "view_platform": "View Platform",
    "choose_role": "Choose Role",
    "start_onboarding": "Start Onboarding",
    "open_support": "Open Support",
    "how_it_works": "How It Works",
    "view_pricing": "View Pricing",
    "request_access": "Request Access",
    "talk_to_sales": "Talk to Sales",
    "view_publishing_flow": "View Publishing Flow",
    "view_tickets": "View Tickets",
    "review_customers": "Review Customers",
    "refresh": "Refresh",
    "open_logs": "Open Logs",
    "submit_feature": "Submit Feature Request",
}

# Names used by the first ViralForge community configuration. These are considered
# cleanup candidates only after an owner explicitly confirms setup-reset.
LEGACY_MANAGED_CHANNEL_NAMES = frozenset(
    {
        "rules",
        "changelog",
        "welcome",
        "feature-overview",
        "plans",
        "platform-status",
        "introductions",
        "showcase",
        "feedback",
        "bug-reports",
        "help",
        "releases",
        "product-updates",
        "community-lounge",
        "resources",
        "customer-lounge",
        "customer-announcements",
        "creator-strategy",
        "creator-resources",
        "bodycams-daily-hq",
        "support-desk",
        "open-a-ticket",
        "ticket-commands",
        "staff-announcements",
        "moderation-queue",
        "support-queue",
        "reported-messages",
        "staff-notes",
        "ops-dashboard",
        "projects",
        "review-inbox",
        "posting-queue",
        "discovery-queue",
        "source-review",
        "media-analysis",
        "clip-candidates",
        "content-packages",
        "publishing-queue",
        "errors",
        "audit-log",
    }
)


def is_guild_owner(interaction: discord.Interaction) -> bool:
    return interaction.guild is not None and interaction.guild.owner_id == interaction.user.id


def _session() -> Session:
    return next(get_session())


def _public_embed(config: dict[str, Any], key: str) -> discord.Embed:
    item = config["embeds"]["embeds"][key]
    description = item["description"]
    eyebrow = item.get("eyebrow")
    if eyebrow:
        description = f"**{eyebrow}**\n{description}"
    embed = discord.Embed(
        title=item["title"],
        description=description,
        color=int(item.get("color", "#ff4d44").lstrip("#"), 16),
    )
    for field in item.get("fields", [])[:4]:
        embed.add_field(name=field["name"], value=field["value"], inline=False)
    branding = config["branding"]
    embed.set_footer(text=branding["footer"])
    icon_name = branding.get("icon_asset")
    if icon_name and (Path(branding["asset_directory"]) / icon_name).is_file():
        embed.set_thumbnail(url=f"attachment://{icon_name}")
    asset = item.get("asset")
    if asset and (Path(branding["asset_directory"]) / asset).is_file():
        embed.set_image(url=f"attachment://{asset}")
    return embed


def _embed_files(config: dict[str, Any], key: str) -> list[discord.File]:
    item, branding = config["embeds"]["embeds"][key], config["branding"]
    root = Path(branding["asset_directory"])
    names = [branding.get("icon_asset"), item.get("asset")]
    return [discord.File(root / name, filename=name) for name in dict.fromkeys(names) if name and (root / name).is_file()]


def _resource(
    session: Session, repo: BusinessRepository, guild: discord.Guild, kind: str, key: str
) -> Any | None:
    identifier = repo.resource_id(session, guild.id, kind, key)
    if identifier is None:
        return None
    return guild.get_channel(identifier) if kind != "role" else guild.get_role(identifier)


def _role_by_key(
    session: Session, repo: BusinessRepository, guild: discord.Guild, key: str
) -> discord.Role | None:
    value = _resource(session, repo, guild, "role", key)
    return value if isinstance(value, discord.Role) else None


def _find_existing(
    guild: discord.Guild, item: ResourcePlan
) -> discord.Role | discord.abc.GuildChannel | None:
    if item.resource_type == "role":
        return discord.utils.get(guild.roles, name=item.name)
    if item.resource_type == "category":
        return discord.utils.get(guild.categories, name=item.name)
    channels = guild.forums if item.kind == "forum" else guild.text_channels
    return discord.utils.get(channels, name=item.name)


def _overwrites(
    session: Session,
    repo: BusinessRepository,
    guild: discord.Guild,
    item: ResourcePlan,
    role_cache: dict[str, discord.Role] | None = None,
) -> dict[discord.Role | discord.Member | discord.Object, discord.PermissionOverwrite]:
    overwrites: dict[
        discord.Role | discord.Member | discord.Object, discord.PermissionOverwrite
    ] = {}
    if item.audience == "public":
        overwrites[guild.default_role] = discord.PermissionOverwrite(
            view_channel=True, send_messages=not item.read_only
        )
    else:
        overwrites[guild.default_role] = discord.PermissionOverwrite(view_channel=False)
        for role_key in audience_role_keys(item.audience):
            role = (role_cache or {}).get(role_key) or _role_by_key(session, repo, guild, role_key)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=not item.read_only
                )
    for role_key in STAFF_ROLE_KEYS:
        role = (role_cache or {}).get(role_key) or _role_by_key(session, repo, guild, role_key)
        if role:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    return overwrites


async def apply_server_plan(guild: discord.Guild, *, apply_changes: bool) -> tuple[list[str], int]:
    """Return a preview unless the guild owner explicitly requests changes.

    Resource matching uses persisted IDs before names, so repeats repair rather than duplicate.
    """
    config = load_config()
    plan = plan_resources(config)
    if not apply_changes:
        return [f"{item.resource_type}:{item.resource_key}" for item in plan], 0
    repo, session = BusinessRepository(), _session()
    changed = 0
    resolved: dict[tuple[str, str], discord.Role | discord.abc.GuildChannel] = {}
    resolved_roles: dict[str, discord.Role] = {}
    try:
        guild_config = repo.guild_config(session, guild.id, guild.name, config["server"]["version"])
        for item in plan:
            existing = (
                resolved.get((item.resource_type, item.resource_key))
                or _resource(session, repo, guild, item.resource_type, item.resource_key)
                or _find_existing(guild, item)
            )
            if item.resource_type == "channel" and existing is not None:
                expected_forum = item.kind == "forum"
                if expected_forum != isinstance(existing, discord.ForumChannel):
                    # Discord channel types are immutable. Retain the legacy managed channel
                    # and replace the resource mapping with a correctly typed successor.
                    existing = None
            if existing is None:
                if item.resource_type == "role":
                    role_config = next(
                        value
                        for value in config["roles"]["roles"]
                        if value["key"] == item.resource_key
                    )
                    existing = await guild.create_role(
                        name=item.name,
                        colour=discord.Colour(int(role_config["color"].lstrip("#"), 16)),
                        reason="ViralForge owner-approved setup",
                    )
                elif item.resource_type == "category":
                    existing = await guild.create_category(
                        item.name,
                        overwrites=_overwrites(session, repo, guild, item, resolved_roles),
                        reason="ViralForge owner-approved setup",
                    )
                else:
                    category = resolved.get(("category", item.category_key or "")) or _resource(
                        session, repo, guild, "category", item.category_key or ""
                    )
                    if not isinstance(category, discord.CategoryChannel):
                        raise DiscordBusinessError(
                            f"missing configured category {item.category_key}"
                        )
                    overwrites = _overwrites(session, repo, guild, item, resolved_roles)
                    if item.kind == "forum":
                        existing = await guild.create_forum(
                            item.name,
                            category=category,
                            overwrites=overwrites,
                            reason="ViralForge owner-approved setup",
                        )
                        if item.tags:
                            await existing.edit(
                                available_tags=[discord.ForumTag(name=tag) for tag in item.tags],
                                reason="ViralForge forum tag configuration",
                            )
                    else:
                        existing = await guild.create_text_channel(
                            item.name,
                            category=category,
                            overwrites=overwrites,
                            reason="ViralForge owner-approved setup",
                        )
                changed += 1
            elif item.resource_type == "role" and isinstance(existing, discord.Role):
                role_config = next(
                    value for value in config["roles"]["roles"] if value["key"] == item.resource_key
                )
                colour = discord.Colour(int(role_config["color"].lstrip("#"), 16))
                if existing.name != item.name or existing.colour != colour:
                    await existing.edit(
                        name=item.name,
                        colour=colour,
                        reason="ViralForge managed role refresh",
                    )
                    changed += 1
            elif item.resource_type == "category" and isinstance(existing, discord.CategoryChannel):
                properties: dict[str, Any] = {}
                if existing.name != item.name:
                    properties["name"] = item.name
                overwrites = _overwrites(session, repo, guild, item, resolved_roles)
                if existing.overwrites != overwrites:
                    properties["overwrites"] = overwrites
                if properties:
                    await existing.edit(reason="ViralForge managed category refresh", **properties)
                    changed += 1
            elif isinstance(existing, (discord.TextChannel, discord.ForumChannel)):
                category = resolved.get(("category", item.category_key or "")) or _resource(
                    session, repo, guild, "category", item.category_key or ""
                )
                if not isinstance(category, discord.CategoryChannel):
                    raise DiscordBusinessError(f"missing configured category {item.category_key}")
                properties = {}
                if existing.name != item.name:
                    properties["name"] = item.name
                if existing.category_id != category.id:
                    properties["category"] = category
                overwrites = _overwrites(session, repo, guild, item, resolved_roles)
                if existing.overwrites != overwrites:
                    properties["overwrites"] = overwrites
                if properties:
                    await existing.edit(reason="ViralForge managed channel refresh", **properties)
                    changed += 1
                if isinstance(existing, discord.ForumChannel) and item.tags:
                    existing_tags = {tag.name for tag in existing.available_tags}
                    if existing_tags != set(item.tags):
                        await existing.edit(
                            available_tags=[discord.ForumTag(name=tag) for tag in item.tags],
                            reason="ViralForge managed forum tag refresh",
                        )
                        changed += 1
            repo.save_resource(session, guild_config, item, existing.id)
            resolved[(item.resource_type, item.resource_key)] = existing
            if item.resource_type == "role" and isinstance(existing, discord.Role):
                resolved_roles[item.resource_key] = existing
        guild_config.setup_state, guild_config.setup_revision = (
            "APPLIED",
            guild_config.setup_revision + 1,
        )
        session.commit()
        return [], changed
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


async def reset_legacy_setup(guild: discord.Guild, *, apply_changes: bool) -> tuple[list[str], int]:
    """Preview or remove only old ViralForge-managed setup resources.

    This intentionally leaves current resources, private tickets, and unrelated user resources intact.
    Discord channel deletion is only performed after the owner repeats with apply_changes=True.
    """
    config = load_config()
    current_resource_keys = {
        (item.resource_type, item.resource_key) for item in plan_resources(config)
    }
    current_forum_names = {
        item.name for item in plan_resources(config) if item.resource_type == "channel" and item.kind == "forum"
    }
    current_embed_keys = set(config["embeds"]["embeds"])
    repo, session = BusinessRepository(), _session()
    actions: list[str] = []
    changed = 0
    try:
        guild_config = repo.guild_config(session, guild.id, guild.name, config["server"]["version"])
        resources = list(
            session.query(DiscordGuildResource)
            .filter(DiscordGuildResource.guild_config_id == guild_config.id)
            .all()
        )
        obsolete = [
            resource
            for resource in resources
            if (resource.resource_type, resource.resource_key) not in current_resource_keys
        ]
        obsolete_channel_ids = {
            int(resource.discord_id)
            for resource in obsolete
            if resource.resource_type == "channel" and resource.discord_id.isdigit()
        }

        for resource in obsolete:
            if resource.resource_type != "channel":
                continue
            channel = guild.get_channel(int(resource.discord_id))
            if isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
                actions.append(f"delete legacy channel #{channel.name}")
                if apply_changes:
                    await channel.delete(reason="ViralForge owner-confirmed legacy setup cleanup")
                    session.delete(resource)
                    changed += 1
            elif apply_changes:
                session.delete(resource)

        orphan_channels: list[discord.TextChannel | discord.ForumChannel] = []
        orphan_channels.extend(guild.text_channels)
        orphan_channels.extend(guild.forums)
        for channel in orphan_channels:
            replaces_legacy_text_channel = (
                isinstance(channel, discord.TextChannel)
                and channel.name in current_forum_names
                and any(forum.name == channel.name for forum in guild.forums)
            )
            if (
                channel.id in obsolete_channel_ids
                or (
                    channel.name not in LEGACY_MANAGED_CHANNEL_NAMES
                    and not replaces_legacy_text_channel
                )
            ):
                continue
            actions.append(f"delete legacy channel #{channel.name}")
            if apply_changes:
                await channel.delete(reason="ViralForge owner-confirmed legacy setup cleanup")
                changed += 1

        for resource in obsolete:
            if resource.resource_type != "category":
                continue
            category = guild.get_channel(int(resource.discord_id))
            if isinstance(category, discord.CategoryChannel):
                if category.channels:
                    actions.append(f"retain non-empty legacy category {category.name}")
                    continue
                actions.append(f"delete empty legacy category {category.name}")
                if apply_changes:
                    await category.delete(reason="ViralForge owner-confirmed legacy setup cleanup")
                    session.delete(resource)
                    changed += 1
            elif apply_changes:
                session.delete(resource)

        for resource in obsolete:
            if resource.resource_type != "role":
                continue
            role = guild.get_role(int(resource.discord_id))
            if role is not None and not role.is_default():
                actions.append(f"delete legacy role @{role.name}")
                if apply_changes:
                    await role.delete(reason="ViralForge owner-confirmed legacy setup cleanup")
                    session.delete(resource)
                    changed += 1
            elif apply_changes:
                session.delete(resource)

        if apply_changes:
            for published in (
                session.query(DiscordPublishedEmbed)
                .filter(DiscordPublishedEmbed.guild_config_id == guild_config.id)
                .all()
            ):
                if published.embed_key not in current_embed_keys:
                    session.delete(published)
            guild_config.setup_state = "APPLIED"
            guild_config.setup_revision += 1
            session.commit()
        return actions, changed
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class RulesAcceptanceView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Accept Rules",
        style=discord.ButtonStyle.success,
        custom_id="viralforge:business:accept-rules",
    )
    async def accept(
        self, interaction: discord.Interaction, _: discord.ui.Button[RulesAcceptanceView]
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Rules can only be accepted in the community server.", ephemeral=True
            )
            return
        config, repo, session = load_config(), BusinessRepository(), _session()
        try:
            guild_config = repo.guild_config(
                session, interaction.guild.id, interaction.guild.name, config["server"]["version"]
            )
            created = repo.accept_rules(session, guild_config, interaction.user.id)
            member_role = _role_by_key(
                session, repo, interaction.guild, config["onboarding"]["member_role"]
            )
            if member_role:
                await interaction.user.add_roles(
                    member_role, reason="Accepted ViralForge community rules"
                )
            session.commit()
        finally:
            session.close()
        await interaction.response.send_message(
            "Rules recorded. You now have Member access."
            if created
            else "You already accepted the current rules.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="Terms",
        style=discord.ButtonStyle.secondary,
        custom_id="viralforge:business:terms",
    )
    async def terms(
        self, interaction: discord.Interaction, _: discord.ui.Button[RulesAcceptanceView]
    ) -> None:
        await interaction.response.send_message(
            "Terms are provided during access provisioning. Open Support if you need a copy.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="Privacy",
        style=discord.ButtonStyle.secondary,
        custom_id="viralforge:business:privacy",
    )
    async def privacy(
        self, interaction: discord.Interaction, _: discord.ui.Button[RulesAcceptanceView]
    ) -> None:
        await interaction.response.send_message(
            "Do not share credentials in Discord. Open Support for any privacy request.",
            ephemeral=True,
        )


class PanelActionButton(discord.ui.Button["PanelActionView"]):
    def __init__(self, panel_key: str, action: str) -> None:
        self.action = action
        super().__init__(
            label=ACTION_LABELS[action],
            style=(
                discord.ButtonStyle.primary
                if action in {"start_onboarding", "open_support", "request_access"}
                else discord.ButtonStyle.secondary
            ),
            custom_id=f"viralforge:business:panel:{panel_key}:{action}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        config = load_config()
        if self.action in {"start_onboarding", "choose_role"}:
            await interaction.response.send_message(
                embed=_public_embed(config, "onboarding"),
                view=OnboardingView(config),
                ephemeral=True,
            )
            return
        if self.action in {"open_support", "request_access", "talk_to_sales"}:
            await _open_ticket(interaction, "general", "normal")
            return
        if self.action == "submit_feature":
            await interaction.response.send_modal(FeedbackModal("feature_requests", "Feature request"))
            return
        channel_key = {
            "view_platform": "product_overview",
            "how_it_works": "how_it_works",
            "view_pricing": "pricing_and_access",
            "view_publishing_flow": "publishing_flow",
            "view_tickets": "ticket_logs",
            "review_customers": "customer_review",
            "open_logs": "ticket_logs",
        }.get(self.action)
        if self.action == "refresh":
            await interaction.response.send_message(
                "Staff refresh is available through `/admin refresh-embeds`.", ephemeral=True
            )
            return
        if channel_key:
            await interaction.response.send_message(
                f"Open the configured **#{channel_key.replace('_', '-')}** channel in this server.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message("That action is not configured.", ephemeral=True)


class PanelActionView(discord.ui.View):
    def __init__(self, panel_key: str, actions: list[str]) -> None:
        super().__init__(timeout=None)
        for action in actions[:5]:
            if action != "accept_rules":
                self.add_item(PanelActionButton(panel_key, action))


class OnboardingSelect(discord.ui.Select["OnboardingView"]):
    def __init__(self, field: str, options: list[str]) -> None:
        self.field = field
        super().__init__(
            placeholder=f"Choose your {field.replace('_', ' ')}",
            options=[
                discord.SelectOption(label=value.replace("_", " "), value=value)
                for value in options
            ],
            custom_id=f"viralforge:business:onboarding:{field}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        assert isinstance(self.view, OnboardingView)
        setattr(self.view, self.field, self.values[0])
        await interaction.response.send_message(
            f"Saved {self.field.replace('_', ' ')}. Choose the other option to finish.",
            ephemeral=True,
        )


class OnboardingView(discord.ui.View):
    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(timeout=300)
        self.account_type: str | None = None
        self.primary_goal: str | None = None
        self.add_item(OnboardingSelect("account_type", config["onboarding"]["account_types"]))
        self.add_item(OnboardingSelect("primary_goal", config["onboarding"]["primary_goals"]))

    @discord.ui.button(label="Finish onboarding", style=discord.ButtonStyle.success)
    async def finish(
        self, interaction: discord.Interaction, _: discord.ui.Button[OnboardingView]
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this in the community server.", ephemeral=True
            )
            return
        repo, session = BusinessRepository(), _session()
        try:
            config = load_config()
            guild_config = repo.guild_config(
                session, interaction.guild.id, interaction.guild.name, config["server"]["version"]
            )
            progress = repo.save_onboarding(
                session, guild_config, interaction.user.id, self.account_type, self.primary_goal
            )
            session.commit()
        finally:
            session.close()
        message = (
            "Onboarding complete. Welcome to the forge."
            if progress.status == "COMPLETED"
            else "Choose an account type and goal first."
        )
        await interaction.response.send_message(message, ephemeral=True)


class FeedbackModal(discord.ui.Modal):
    detail: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
        label="Details", style=discord.TextStyle.paragraph, max_length=1500
    )

    def __init__(self, target_key: str, title: str) -> None:
        super().__init__(title=title)
        self.target_key = target_key

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this in the community server.", ephemeral=True
            )
            return
        repo, session = BusinessRepository(), _session()
        try:
            target = _resource(session, repo, interaction.guild, "channel", self.target_key)
        finally:
            session.close()
        if isinstance(target, discord.TextChannel):
            await target.send(
                f"New submission from {interaction.user.mention}:\n{self.detail.value}",
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
        elif isinstance(target, discord.ForumChannel):
            await target.create_thread(
                name=f"{self.title} — {interaction.user.display_name}"[:100],
                content=self.detail.value,
                reason="ViralForge structured community submission",
            )
        else:
            await interaction.response.send_message(
                "That request destination is not configured yet. Open Support instead.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "Thanks — your submission was sent to the team.", ephemeral=True
        )


async def _open_ticket(interaction: discord.Interaction, ticket_type: str, priority: str) -> None:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Use this in the community server.", ephemeral=True)
        return
    config, repo, session = load_config(), BusinessRepository(), _session()
    try:
        guild_config = repo.guild_config(
            session, interaction.guild.id, interaction.guild.name, config["server"]["version"]
        )
        category = _resource(
            session, repo, interaction.guild, "category", config["tickets"]["private_category_key"]
        )
        if not isinstance(category, discord.CategoryChannel):
            await interaction.response.send_message(
                "Support has not been configured yet.", ephemeral=True
            )
            return
        overwrites: dict[
            discord.Role | discord.Member | discord.Object, discord.PermissionOverwrite
        ] = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        for role_key in config["tickets"]["staff_roles"]:
            role = _role_by_key(session, repo, interaction.guild, role_key)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, manage_messages=True
                )
        channel = await interaction.guild.create_text_channel(
            f"ticket-{interaction.user.id}-{ticket_type}",
            category=category,
            overwrites=overwrites,
            reason="ViralForge support ticket",
        )
        ticket = repo.open_ticket(
            session, guild_config, channel.id, interaction.user.id, ticket_type, priority
        )
        session.commit()
    finally:
        session.close()
    await channel.send(
        f"Ticket #{ticket.ticket_number} opened for {interaction.user.mention}. Please do not share credentials or private source media."
    )
    await interaction.response.send_message(
        f"Private support ticket created: {channel.mention}", ephemeral=True
    )


async def publish_public_embeds(guild: discord.Guild) -> int:
    """Create/update official public messages without touching community conversation history."""
    config, repo, session = load_config(), BusinessRepository(), _session()
    sent = 0
    try:
        guild_config = repo.guild_config(session, guild.id, guild.name, config["server"]["version"])
        for embed_key, panel in config["embeds"]["embeds"].items():
            channel_key = panel["channel"]
            channel = _resource(session, repo, guild, "channel", channel_key)
            if not isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
                continue
            embed = _public_embed(config, embed_key)
            files = _embed_files(config, embed_key)
            view: discord.ui.View | None
            view = (
                RulesAcceptanceView()
                if "accept_rules" in panel.get("actions", [])
                else PanelActionView(embed_key, panel.get("actions", []))
            )
            saved = repo.published_embed(session, guild_config, embed_key)
            message = None
            if saved:
                try:
                    if isinstance(channel, discord.ForumChannel):
                        thread = guild.get_thread(int(saved.discord_message_id))
                        if thread is None:
                            fetched = await guild.fetch_channel(int(saved.discord_message_id))
                            thread = fetched if isinstance(fetched, discord.Thread) else None
                        if thread is not None:
                            message = await thread.fetch_message(int(saved.discord_message_id))
                    else:
                        message = await channel.fetch_message(int(saved.discord_message_id))
                    if message is not None:
                        await message.edit(embed=embed, view=view, attachments=files)
                except discord.NotFound:
                    message = None
            if message is None:
                if isinstance(channel, discord.ForumChannel):
                    post = await channel.create_thread(
                        name=panel["title"][:100], embed=embed, view=view, files=files
                    )
                    message = post.message
                else:
                    message = await channel.send(embed=embed, view=view, files=files)
            repo.save_embed(session, guild_config, embed_key, channel_key, message.id)
            sent += 1
        session.commit()
        return sent
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _business_status(guild: discord.Guild) -> str:
    config, session = load_config(), _session()
    try:
        row = session.query(DiscordGuildConfig).filter_by(guild_id=str(guild.id)).one_or_none()
        count = len(plan_resources(config))
        return f"Config {config['server']['version']} • {count} defined resources • state: {row.setup_state if row else 'PREVIEW'}"
    finally:
        session.close()


async def apply_business_presence(bot: Any, position: int = 0) -> int:
    """Apply one configured activity. Rich-presence asset keys remain config-only for portal setup."""
    config = load_config()
    activities = config["branding"].get("presence", {}).get("activities", [])
    if not activities:
        return position
    index = position % len(activities)
    item = activities[index]
    activity_type = {
        "playing": discord.ActivityType.playing,
        "watching": discord.ActivityType.watching,
        "listening": discord.ActivityType.listening,
        "competing": discord.ActivityType.competing,
    }.get(item.get("type"), discord.ActivityType.playing)
    await bot.change_presence(activity=discord.Activity(type=activity_type, name=item["name"]))
    return index + 1


def business_presence_interval() -> int:
    return int(load_config()["branding"].get("presence", {}).get("rotation_seconds", 45))


def _reset_summary(actions: list[str], changed: int, apply_changes: bool) -> str:
    preview = "\n".join(f"• {action}" for action in actions[:12]) or "No legacy setup resources found."
    suffix = "" if len(actions) <= 12 else f"\n… and {len(actions) - 12} more."
    if apply_changes:
        return (
            f"Legacy cleanup completed: {changed} managed resources removed. "
            "Current setup resources, private tickets, and unrelated channels were preserved."
        )
    return (
        f"Dry-run: {len(actions)} legacy cleanup actions found. "
        "Nothing was deleted. Re-run with `apply_changes: True` to confirm.\n"
        f"{preview}{suffix}"
    )


def register_business_commands(bot: Any) -> None:
    """Attach separate public/customer/admin groups without consuming the operational group budget."""
    if getattr(bot, "_business_commands_registered", False):
        return
    bot._business_commands_registered = True
    bot.add_view(RulesAcceptanceView())
    for panel_key, panel in load_config()["embeds"]["embeds"].items():
        if "accept_rules" not in panel.get("actions", []):
            bot.add_view(PanelActionView(panel_key, panel.get("actions", [])))
    company = app_commands.Group(
        name="company", description="Public ViralForge company and platform information"
    )
    support = app_commands.Group(name="support", description="Private support and feedback")
    account = app_commands.Group(
        name="account", description="Customer onboarding and account readiness"
    )
    admin = app_commands.Group(
        name="admin", description="Owner-only ViralForge server administration"
    )
    bot.tree.add_command(company)
    bot.tree.add_command(support)
    bot.tree.add_command(account)
    bot.tree.add_command(admin)

    @bot.tree.command(name="setup", description="Owner-only ViralForge server setup or refresh")
    @app_commands.describe(apply_changes="Create or refresh managed server resources and panels")
    async def setup(interaction: discord.Interaction, apply_changes: bool = False) -> None:
        if not is_guild_owner(interaction) or interaction.guild is None:
            await interaction.response.send_message(
                "Only the Discord server owner can run setup.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            preview, changed = await apply_server_plan(
                interaction.guild, apply_changes=apply_changes
            )
            panels = await publish_public_embeds(interaction.guild) if apply_changes else 0
        except (discord.DiscordException, DiscordBusinessError) as error:
            await interaction.followup.send(f"Setup stopped safely: {error}", ephemeral=True)
            return
        await interaction.followup.send(
            f"Setup complete: {changed} managed resources refreshed and {panels} official panels published. Unmanaged resources were preserved."
            if apply_changes
            else f"Dry-run: {len(preview)} resources would be created or repaired. Re-run with `apply_changes: True` to apply.",
            ephemeral=True,
        )

    @bot.tree.command(name="setup-reset", description="Owner-only cleanup of legacy ViralForge setup")
    @app_commands.describe(apply_changes="Delete only previewed legacy ViralForge-managed resources")
    async def setup_reset(interaction: discord.Interaction, apply_changes: bool = False) -> None:
        if not is_guild_owner(interaction) or interaction.guild is None:
            await interaction.response.send_message(
                "Only the Discord server owner can run setup cleanup.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            actions, changed = await reset_legacy_setup(
                interaction.guild, apply_changes=apply_changes
            )
        except (discord.DiscordException, DiscordBusinessError) as error:
            await interaction.followup.send(f"Cleanup stopped safely: {error}", ephemeral=True)
            return
        await interaction.followup.send(
            _reset_summary(actions, changed, apply_changes), ephemeral=True
        )

    @company.command(name="about", description="What ViralForge is")
    async def about(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "**ViralForge** — The creator intelligence forge: source review, media intelligence, clip review, content packages, and explicit human-controlled publishing.",
            ephemeral=True,
        )

    @company.command(name="features", description="Explore platform capabilities")
    async def features(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "Discovery, source-quality review, analysis, clip opportunities, editable content packages, multi-brand controls, review-first publishing, and analytics feedback. No public action is automatic.",
            ephemeral=True,
        )

    @company.command(name="plans", description="Show subscription readiness options")
    async def plans(interaction: discord.Interaction) -> None:
        plans = load_config()["plans"]["plans"]
        await interaction.response.send_message(
            "\n".join(f"**{item['name']}** — {item['description']}" for item in plans),
            ephemeral=True,
        )

    @company.command(name="roadmap", description="Show the safe public product direction")
    async def roadmap(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "ViralForge evolves through operator-reviewed milestones. Roadmap discussions do not imply delivery dates or automatic public publishing.",
            ephemeral=True,
        )

    @company.command(name="docs", description="Find ViralForge documentation")
    async def docs(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "Documentation is available through the ViralForge workspace and this server’s #resources channel. Never share credentials in documentation requests.",
            ephemeral=True,
        )

    @company.command(name="status", description="Show public platform status")
    async def company_status(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "Status visibility is read-only here. Operators review runtime health in the private control plane.",
            ephemeral=True,
        )

    @support.command(name="open", description="Open a private support ticket")
    @app_commands.describe(
        ticket_type="Account, billing, technical, onboarding, safety, or other",
        priority="Normal or priority",
    )
    async def support_open(
        interaction: discord.Interaction, ticket_type: str = "account", priority: str = "normal"
    ) -> None:
        config = load_config()
        if (
            ticket_type not in config["tickets"]["ticket_types"]
            or priority not in config["tickets"]["priority_levels"]
        ):
            await interaction.response.send_message(
                "Choose a supported ticket type and priority.", ephemeral=True
            )
            return
        await _open_ticket(interaction, ticket_type, priority)

    @support.command(name="feature", description="Send a structured feature request")
    async def feature(interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(FeedbackModal("feature_requests", "Feature request"))

    @support.command(name="bug", description="Send a structured bug report")
    async def bug(interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(FeedbackModal("feature_requests", "Bug report"))

    @support.command(name="history", description="View your ticket history")
    async def history(interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this in the community server.", ephemeral=True
            )
            return
        repo, session = BusinessRepository(), _session()
        try:
            row = (
                session.query(DiscordGuildConfig)
                .filter_by(guild_id=str(interaction.guild.id))
                .one_or_none()
            )
            tickets = repo.tickets_for_user(session, row, interaction.user.id) if row else []
        finally:
            session.close()
        await interaction.response.send_message(
            "\n".join(
                f"#{ticket.ticket_number} • {ticket.ticket_type} • {ticket.status}"
                for ticket in tickets
            )
            or "No tickets yet.",
            ephemeral=True,
        )

    @account.command(name="get-started", description="Choose your ViralForge onboarding path")
    async def get_started(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=_public_embed(load_config(), "start_here"),
            view=OnboardingView(load_config()),
            ephemeral=True,
        )

    @account.command(name="subscription", description="Show your provisioned subscription state")
    async def subscription(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "Subscription billing is not enabled. Access is provisioned by an operator and never inferred from a Discord role.",
            ephemeral=True,
        )

    @account.command(name="billing-status", description="Show safe billing readiness status")
    async def billing_status(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "Billing is not enabled in Discord. No payment data is collected or stored by this bot.",
            ephemeral=True,
        )

    @account.command(name="upgrade", description="Request an access upgrade")
    async def upgrade(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "To request an upgrade, open a private Support ticket. An operator must explicitly verify and provision access.",
            ephemeral=True,
        )

    @account.command(name="usage", description="Show usage visibility policy")
    async def usage(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "Usage data is not exposed in public Discord. Customers receive only their explicitly provisioned workspace context.",
            ephemeral=True,
        )

    @account.command(name="connect-account", description="Request a verified customer account link")
    async def connect_account(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "Account linking is performed only after Support verifies the customer identity. Discord never receives raw external credentials.",
            ephemeral=True,
        )

    @account.command(name="workspace", description="Show your linked workspace")
    async def workspace(interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this in the community server.", ephemeral=True
            )
            return
        repo, session = BusinessRepository(), _session()
        try:
            row = (
                session.query(DiscordGuildConfig)
                .filter_by(guild_id=str(interaction.guild.id))
                .one_or_none()
            )
            link = repo.customer_link(session, row, interaction.user.id) if row else None
        finally:
            session.close()
        await interaction.response.send_message(
            "No workspace is linked to this Discord account yet. Ask Support to verify access."
            if link is None
            else f"Linked workspace: `{link.workspace_id}` • brand: `{link.brand_id or 'none'}`",
            ephemeral=True,
        )

    @admin.command(name="setup-server", description="Owner-only dry-run or idempotent server setup")
    async def setup_server(interaction: discord.Interaction, apply_changes: bool = False) -> None:
        if not is_guild_owner(interaction):
            await interaction.response.send_message(
                "Only the Discord server owner can run setup.", ephemeral=True
            )
            return
        if interaction.guild is None:
            await interaction.response.send_message("Use this in a Discord server.", ephemeral=True)
            return
        # The applied plan may create dozens of Discord resources. Acknowledge before
        # Discord's interaction deadline, then send the final result ephemerally.
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            preview, changed = await apply_server_plan(
                interaction.guild, apply_changes=apply_changes
            )
            panels = await publish_public_embeds(interaction.guild) if apply_changes else 0
        except (discord.DiscordException, DiscordBusinessError) as error:
            await interaction.followup.send(f"Setup stopped safely: {error}", ephemeral=True)
            return
        message = (
            f"Applied or refreshed {changed} managed resources and {panels} official panels. Existing unmanaged resources were retained."
            if apply_changes
            else f"Dry-run: {len(preview)} resources would be created or repaired. Re-run with `apply_changes: True` to apply."
        )
        await interaction.followup.send(message, ephemeral=True)

    @admin.command(name="setup-reset", description="Owner-only cleanup of legacy ViralForge setup")
    async def admin_setup_reset(
        interaction: discord.Interaction, apply_changes: bool = False
    ) -> None:
        if not is_guild_owner(interaction) or interaction.guild is None:
            await interaction.response.send_message(
                "Only the Discord server owner can run setup cleanup.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            actions, changed = await reset_legacy_setup(
                interaction.guild, apply_changes=apply_changes
            )
        except (discord.DiscordException, DiscordBusinessError) as error:
            await interaction.followup.send(f"Cleanup stopped safely: {error}", ephemeral=True)
            return
        await interaction.followup.send(
            _reset_summary(actions, changed, apply_changes), ephemeral=True
        )

    @admin.command(name="setup-status", description="Owner-only Discord setup status")
    async def setup_status(interaction: discord.Interaction) -> None:
        if not is_guild_owner(interaction) or interaction.guild is None:
            await interaction.response.send_message(
                "Only the Discord server owner can view setup status.", ephemeral=True
            )
            return
        await interaction.response.send_message(_business_status(interaction.guild), ephemeral=True)

    @admin.command(
        name="setup-repair",
        description="Owner-only idempotent repair of missing configured resources",
    )
    async def setup_repair(interaction: discord.Interaction, apply_changes: bool = False) -> None:
        if not is_guild_owner(interaction) or interaction.guild is None:
            await interaction.response.send_message(
                "Only the Discord server owner can repair setup.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        preview, changed = await apply_server_plan(interaction.guild, apply_changes=apply_changes)
        await interaction.followup.send(
            f"Repaired {changed} resources."
            if apply_changes
            else f"Dry-run repair: {len(preview)} resources checked. Re-run with `apply_changes: True` to apply.",
            ephemeral=True,
        )

    @admin.command(
        name="setup-reset-preview",
        description="Owner-only reset of setup preview state; does not delete Discord resources",
    )
    async def setup_reset_preview(interaction: discord.Interaction) -> None:
        if not is_guild_owner(interaction) or interaction.guild is None:
            await interaction.response.send_message(
                "Only the Discord server owner can reset preview state.", ephemeral=True
            )
            return
        session = _session()
        try:
            row = (
                session.query(DiscordGuildConfig)
                .filter_by(guild_id=str(interaction.guild.id))
                .one_or_none()
            )
            if row:
                row.setup_state = "PREVIEW"
                session.commit()
        finally:
            session.close()
        await interaction.response.send_message(
            "Preview state reset. No channels, roles, messages, tickets, or customer links were deleted.",
            ephemeral=True,
        )

    @admin.command(
        name="refresh-embeds",
        description="Owner-only refresh of official public information embeds",
    )
    async def refresh_embeds(interaction: discord.Interaction) -> None:
        if not is_guild_owner(interaction) or interaction.guild is None:
            await interaction.response.send_message(
                "Only the Discord server owner can refresh public embeds.", ephemeral=True
            )
            return
        count = await publish_public_embeds(interaction.guild)
        await interaction.response.send_message(
            f"Published {count} official embeds. Community history was not modified.",
            ephemeral=True,
        )

    @admin.command(
        name="setup-export", description="Owner-only configuration summary without secrets"
    )
    async def setup_export(interaction: discord.Interaction) -> None:
        if not is_guild_owner(interaction) or interaction.guild is None:
            await interaction.response.send_message(
                "Only the Discord server owner can export setup details.", ephemeral=True
            )
            return
        config = load_config()
        plan = plan_resources(config)
        await interaction.response.send_message(
            f"ViralForge Discord config v{config['server']['version']}\nRoles: {sum(item.resource_type == 'role' for item in plan)}\nCategories: {sum(item.resource_type == 'category' for item in plan)}\nChannels: {sum(item.resource_type == 'channel' for item in plan)}\nNo credentials are stored or exported.",
            ephemeral=True,
        )

    @admin.command(name="config-check", description="Owner-only config validity check")
    async def config_check(interaction: discord.Interaction) -> None:
        if not is_guild_owner(interaction):
            await interaction.response.send_message(
                "Only the Discord server owner can check configuration.", ephemeral=True
            )
            return
        try:
            count = len(plan_resources(load_config()))
        except DiscordBusinessError as error:
            await interaction.response.send_message(
                f"Configuration invalid: {error}", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Configuration is valid; {count} resources are defined. No secrets were read or displayed.",
            ephemeral=True,
        )
