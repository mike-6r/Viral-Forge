from pathlib import Path

from sqlalchemy import inspect

from app.discord_bot import friendly_project_status
from app.discord_business.discord import (
    LEGACY_MANAGED_CHANNEL_NAMES,
    PanelActionButton,
    _panel_embeds,
    _private_ticket_embed,
    _private_ticket_embeds,
    _setup_summary,
)
from app.discord_business.models import DiscordGuildConfig, DiscordTicket
from app.discord_business.service import (
    BusinessRepository,
    audience_role_keys,
    load_config,
    plan_resources,
)


def test_discord_business_configuration_is_complete_and_plans_premium_control_plane():
    config = load_config(Path("config/discord"))
    resources = plan_resources(config)
    keys = {(item.resource_type, item.resource_key) for item in resources}
    assert {
        ("role", "owner"),
        ("role", "operations_lead"),
        ("role", "customer"),
        ("role", "brand"),
        ("channel", "welcome"),
        ("channel", "ops_center"),
        ("channel", "ready_to_post"),
        ("channel", "feature_requests"),
    } <= keys
    assert next(item for item in resources if item.resource_key == "ops_center").audience == "staff"
    assert next(
        item for item in resources if item.resource_type == "channel" and item.resource_key == "welcome"
    ).read_only
    assert next(item for item in resources if item.resource_key == "feature_requests").kind == "forum"
    assert next(item for item in resources if item.resource_key == "feature_requests").tags == (
        "Feature",
        "Integration",
        "Workflow",
        "Bug",
        "Quality of Life",
        "In Review",
        "Planned",
    )


def test_rules_onboarding_and_ticket_persistence_are_idempotent(session):
    repo = BusinessRepository()
    config = repo.guild_config(session, 123, "Test Guild", "1.0")
    assert repo.accept_rules(session, config, 456) is True
    assert repo.accept_rules(session, config, 456) is False
    progress = repo.save_onboarding(session, config, 456, "creator", "review_clips")
    ticket = repo.open_ticket(session, config, 789, 456, "technical", "normal")
    session.commit()
    assert progress.status == "COMPLETED"
    assert ticket.ticket_number == 1
    assert repo.open_ticket(session, config, 789, 456, "technical", "normal").id == ticket.id


def test_discord_business_models_are_registered_for_schema_creation(session):
    names = set(inspect(session.bind).get_table_names())
    assert {
        "discord_guild_configs",
        "discord_guild_resources",
        "discord_rules_acceptances",
        "discord_tickets",
        "discord_onboarding_progress",
        "discord_customer_links",
        "discord_published_embeds",
    } <= names
    assert session.query(DiscordGuildConfig).count() == 0


def test_premium_panel_configuration_references_existing_assets_and_managed_channels():
    config = load_config(Path("config/discord"))
    resource_keys = {
        item.resource_key for item in plan_resources(config) if item.resource_type == "channel"
    }
    asset_root = Path(config["branding"]["asset_directory"])
    assert (asset_root / config["branding"]["icon_asset"]).is_file()
    assert set(config["embeds"]["embeds"]) == {
        "welcome",
        "standards",
        "choose_roles",
        "announcements",
        "product_overview",
        "how_it_works",
        "pricing",
        "workspace_guide",
        "review_and_publish",
        "analytics",
        "support",
        "onboarding",
        "feature_requests",
        "ops_center",
        "ready_to_post",
    }
    hero_panels = set()
    for key, panel in config["embeds"]["embeds"].items():
        assert panel["channel"] in resource_keys
        if panel.get("asset"):
            assert (asset_root / panel["asset"]).is_file()
        if panel.get("hero_asset"):
            hero_panels.add(key)
        assert len(panel.get("fields", [])) <= 4
        assert len(panel.get("actions", [])) <= 5
    assert hero_panels == {
        "welcome",
        "choose_roles",
        "product_overview",
        "how_it_works",
        "pricing",
        "workspace_guide",
        "review_and_publish",
        "analytics",
        "support",
        "ops_center",
    }
    assert {
        "welcome": "viralforge-welcome-hero.png",
        "choose_roles": "viralforge-access-hero.png",
        "product_overview": "viralforge-workflow-hero.png",
        "how_it_works": "viralforge-workflow-hero.png",
        "pricing": "viralforge-plans-hero.png",
        "workspace_guide": "viralforge-workspace-hero.png",
        "review_and_publish": "viralforge-review-hero.png",
        "analytics": "viralforge-analytics-hero.png",
        "support": "viralforge-support-hero.png",
        "ops_center": "viralforge-ops-center-hero.png",
    } == {
        key: panel["asset"]
        for key, panel in config["embeds"]["embeds"].items()
        if panel.get("hero_asset")
    }
    assert config["tickets"]["hero_asset"] == "viralforge-ticket-hero.png"
    assert (asset_root / config["tickets"]["hero_asset"]).is_file()
    manifest = (asset_root / "ASSET_MANIFEST.md").read_text(encoding="utf-8")
    for asset in {
        panel["asset"]
        for panel in config["embeds"]["embeds"].values()
        if panel.get("hero_asset")
    } | {config["tickets"]["hero_asset"]}:
        assert f"`{asset}`" in manifest


def test_major_landing_panels_publish_image_only_hero_before_compact_content_card():
    config = load_config(Path("config/discord"))
    panels = config["embeds"]["embeds"]
    expected = {
        "welcome",
        "choose_roles",
        "product_overview",
        "how_it_works",
        "pricing",
        "workspace_guide",
        "review_and_publish",
        "analytics",
        "support",
        "ops_center",
    }
    for key in expected:
        assert panels[key]["layout"] == "hero_then_content"
        hero, content = _panel_embeds(config, key)
        assert hero.title is None
        assert hero.description is None
        assert not hero.fields
        assert hero.footer.text is None
        assert hero.author.name is None
        assert hero.thumbnail.url is None
        assert hero.image.url == f"attachment://{panels[key]['asset']}"
        assert content.image.url is None
        assert content.footer.text is None
        assert content.author.name is None
        assert len(content.fields) <= 3
        assert all(field.inline for field in content.fields)


def test_discord_landing_cards_are_compact_and_follow_the_product_action_order():
    config = load_config(Path("config/discord"))
    panels = config["embeds"]["embeds"]
    expected_copy = {
        "welcome": ("ViralForge", "**PLATFORM**", ["Submit", "Review", "Decide"]),
        "product_overview": (
            "One Content Workflow",
            "**PLATFORM**",
            ["Source", "Review", "Decision"],
        ),
        "how_it_works": (
            "Discover → Clip → Review → Decide",
            "**WORKFLOW**",
            ["Discover", "Clip", "Decide"],
        ),
        "choose_roles": ("Set Up Access", "**ACCESS**", ["Account", "Alerts", "Workspace"]),
        "pricing": ("Operating Levels", "**PLANS**", ["Creator", "Agency", "Enterprise"]),
        "support": ("Open the Right Request", "**SUPPORT**", ["Access", "Workflow", "Issues"]),
        "ops_center": ("Operations Center", "**OPERATIONS**", ["Attention", "Queue", "Support"]),
    }
    for key, (title, eyebrow, field_names) in expected_copy.items():
        panel = panels[key]
        assert panel["title"] == title
        assert panel["description"].startswith(eyebrow)
        assert [field["name"] for field in panel["fields"]] == field_names
        assert len(panel["fields"]) == 3
        assert not panel["show_footer"]

    assert panels["welcome"]["actions"] == ["choose_role", "how_it_works", "open_support"]
    assert panels["product_overview"]["actions"] == [
        "start_onboarding",
        "how_it_works",
        "view_pricing",
    ]
    assert panels["ops_center"]["actions"] == [
        "open_review_queue",
        "ready_to_post",
        "add_video",
        "open_tickets",
        "refresh",
    ]
    assert panels["announcements"]["layout"] == "compact"
    assert panels["ready_to_post"]["layout"] == "compact"


def test_panel_buttons_have_one_clear_primary_action_per_product_flow():
    primary = PanelActionButton("welcome", "choose_role")
    secondary = PanelActionButton("welcome", "open_support")
    support_primary = PanelActionButton("support", "open_ticket")
    operations_primary = PanelActionButton("ops_center", "open_review_queue")
    operations_secondary = PanelActionButton("ops_center", "refresh")

    assert primary.style.value == 1
    assert secondary.style.value == 2
    assert support_primary.style.value == 1
    assert operations_primary.style.value == 1
    assert operations_secondary.style.value == 2


def test_discord_saas_polish_has_clean_categories_and_twenty_channels():
    config = load_config(Path("config/discord"))
    resources = plan_resources(config)
    assert len(config["server"]["categories"]) == 7
    assert len(config["channels"]["channels"]) == 20
    assert len({(item.resource_type, item.resource_key) for item in resources}) == len(resources)
    assert [item["key"] for item in config["server"]["categories"]] == [
        "start",
        "platform",
        "workspaces",
        "customers",
        "community",
        "operations",
        "private_requests",
    ]
    assert [item["name"] for item in config["channels"]["channels"]] == [
        "welcome",
        "access",
        "announcements",
        "overview",
        "how-it-works",
        "plans",
        "workspace-guide",
        "review-and-publish",
        "analytics",
        "onboarding",
        "support",
        "feature-requests",
        "general",
        "creator-talk",
        "wins",
        "ops-center",
        "review-queue",
        "ready-to-post",
        "operator-alerts",
        "ticket-logs",
    ]
    assert not any(item["category"] == "private_requests" for item in config["channels"]["channels"])
    assert not any("test" in item["name"] or "demo" in item["name"] for item in config["channels"]["channels"])


def test_legacy_cleanup_candidates_do_not_overlap_current_channel_names():
    config = load_config(Path("config/discord"))
    current_names = {
        item.name for item in plan_resources(config) if item.resource_type == "channel"
    }
    assert not LEGACY_MANAGED_CHANNEL_NAMES & current_names


def test_rules_acceptance_gates_workspaces_but_leaves_public_pages_visible():
    config = load_config(Path("config/discord"))
    category_audiences = {
        item["key"]: item["audience"] for item in config["server"]["categories"]
    }
    assert category_audiences["start"] == "public"
    assert {category_audiences[key] for key in ("platform", "customers", "community")} == {"public"}
    assert category_audiences["workspaces"] == "member"
    assert category_audiences["operations"] == "staff"
    assert category_audiences["private_requests"] == "staff"
    for channel in config["channels"]["channels"]:
        if channel["category"] in {"start", "platform", "customers", "community"}:
            assert channel["audience"] == "public"
        elif channel["category"] not in {"operations", "private_requests"}:
            assert channel["audience"] == "member"


def test_discord_premium_role_and_forum_boundaries_are_configured():
    config = load_config(Path("config/discord"))
    names = {item["key"] for item in config["roles"]["roles"]}
    assert {"member", "customer", "creator", "brand", "agency"} <= names
    assert set(config["role_panels"]["panels"]["notifications"]) == {
        "product_updates",
        "workflow_alerts",
        "creator_tips",
        "case_studies",
        "community_events",
    }
    assert set(config["role_panels"]["account_type_role_keys"]) == {
        "creator",
        "agency",
        "brand",
    }
    assert not set(config["role_panels"]["account_type_role_keys"]) & {
        "customer",
    }
    channels = {item["key"]: item for item in config["channels"]["channels"]}
    assert channels["feature_requests"]["tags"] == [
        "Feature", "Integration", "Workflow", "Bug", "Quality of Life", "In Review", "Planned"
    ]
    assert config["embeds"]["embeds"]["choose_roles"]["actions"] == []
    assert set(config["tickets"]["ticket_types"]) >= {"general", "bug_report", "feature_request"}


def test_private_ticket_panel_uses_the_final_ticket_hero_banner():
    config = load_config(Path("config/discord"))
    ticket = DiscordTicket(ticket_type="workspace_setup")
    content = _private_ticket_embed(config, ticket)
    hero, embed = _private_ticket_embeds(config, ticket)

    assert embed.title == "Private Support"
    assert content.image.url is None
    assert hero.title is None
    assert hero.description is None
    assert not hero.fields
    assert hero.footer.text is None
    assert hero.author.name is None
    assert hero.thumbnail.url is None
    assert hero.image.url == "attachment://viralforge-ticket-hero.png"
    assert [field.name for field in embed.fields] == ["Issue", "Status", "Next Step"]
    assert all(field.inline for field in embed.fields)


def test_public_and_staff_audiences_have_strict_role_boundaries():
    assert audience_role_keys("public") == set()
    assert audience_role_keys("member") == {"member"}
    assert audience_role_keys("staff") == {
        "owner",
        "administrator",
        "operations_lead",
        "content_operator",
        "customer_success",
        "support_team",
        "developer",
    }


def test_setup_summary_lists_legacy_demo_candidates_without_deleting_them():
    config = load_config(Path("config/discord"))
    message = _setup_summary(
        config,
        changed=0,
        panels=0,
        legacy_actions=["delete legacy channel #test", "delete legacy channel #review"],
        applied=False,
    )
    assert "#test" in message
    assert "#review" in message
    assert "nothing is removed" in message.lower()


def test_normal_workflow_statuses_are_human_readable():
    assert friendly_project_status("SOURCE_READY") == "Source Added"
    assert friendly_project_status("SOURCE_REVIEW_REQUIRED") == "Source Review Needed"
    assert friendly_project_status("DOWNLOADING") == "Downloading Video"
    assert friendly_project_status("ANALYZING") == "Analyzing Content"
    assert friendly_project_status("CLIPS_SUGGESTED") == "Clip Suggestions Ready"
    assert friendly_project_status("RENDERING") == "Preparing Clips"
    assert friendly_project_status("RENDERED") == "Clips Ready"
    assert friendly_project_status("CONTENT_READY") == "Content Package Ready"
    assert friendly_project_status("READY_TO_POST") == "Ready for Decision"
    assert friendly_project_status("PENDING") == "Awaiting Review"
    assert friendly_project_status("RUNNING") == "In Progress"
    assert friendly_project_status("UNKNOWN") == "Needs Review"
    assert friendly_project_status("FAILED") == "Needs Attention"
