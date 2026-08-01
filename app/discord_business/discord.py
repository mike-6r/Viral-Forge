"""Discord-facing business platform. Public flows never invoke production controls."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from sqlalchemy.orm import Session

from app.common.db import get_session
from app.discord_business.models import DiscordGuildConfig
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


def is_guild_owner(interaction: discord.Interaction) -> bool:
    return interaction.guild is not None and interaction.guild.owner_id == interaction.user.id


def _session() -> Session:
    return next(get_session())


def _public_embed(config: dict[str, Any], key: str) -> discord.Embed:
    item = config["embeds"]["embeds"][key]
    return discord.Embed(
        title=item["title"],
        description=item["description"],
        color=int(item.get("color", "#ff4d44").lstrip("#"), 16),
    )


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
    return discord.utils.get(guild.text_channels, name=item.name)


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
    for role_key in {"owner", "operator", "admin", "moderator", "support", "community_manager"}:
        role = (role_cache or {}).get(role_key) or _role_by_key(session, repo, guild, role_key)
        if role and item.audience not in {"operator"}:
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
                    existing = await guild.create_text_channel(
                        item.name,
                        category=category,
                        overwrites=_overwrites(session, repo, guild, item, resolved_roles),
                        reason="ViralForge owner-approved setup",
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
    mapping = {
        "welcome": "welcome",
        "rules": "rules",
        "start_here": "start_here",
        "support": "support_desk",
        "status": "platform_status",
    }
    sent = 0
    try:
        guild_config = repo.guild_config(session, guild.id, guild.name, config["server"]["version"])
        for embed_key, channel_key in mapping.items():
            channel = _resource(session, repo, guild, "channel", channel_key)
            if not isinstance(channel, discord.TextChannel):
                continue
            embed = _public_embed(config, embed_key)
            saved = repo.published_embed(session, guild_config, embed_key)
            message = None
            if saved:
                try:
                    message = await channel.fetch_message(int(saved.discord_message_id))
                    await message.edit(
                        embed=embed, view=RulesAcceptanceView() if embed_key == "rules" else None
                    )
                except discord.NotFound:
                    message = None
            if message is None:
                if embed_key == "rules":
                    message = await channel.send(embed=embed, view=RulesAcceptanceView())
                else:
                    message = await channel.send(embed=embed)
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


def register_business_commands(bot: Any) -> None:
    """Attach separate public/customer/admin groups without consuming the operational group budget."""
    if getattr(bot, "_business_commands_registered", False):
        return
    bot._business_commands_registered = True
    bot.add_view(RulesAcceptanceView())
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
        await interaction.response.send_modal(FeedbackModal("bug_reports", "Bug report"))

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
        except (discord.DiscordException, DiscordBusinessError) as error:
            await interaction.followup.send(f"Setup stopped safely: {error}", ephemeral=True)
            return
        message = (
            f"Applied {changed} missing resources. Existing resources were retained."
            if apply_changes
            else f"Dry-run: {len(preview)} resources would be created or repaired. Re-run with `apply_changes: True` to apply."
        )
        await interaction.followup.send(message, ephemeral=True)

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
