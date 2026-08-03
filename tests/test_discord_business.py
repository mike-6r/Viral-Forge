from pathlib import Path

from sqlalchemy import inspect

from app.discord_business.discord import LEGACY_MANAGED_CHANNEL_NAMES
from app.discord_business.models import DiscordGuildConfig
from app.discord_business.service import BusinessRepository, load_config, plan_resources


def test_discord_business_configuration_is_complete_and_plans_premium_control_plane():
    config = load_config(Path("config/discord"))
    resources = plan_resources(config)
    keys = {(item.resource_type, item.resource_key) for item in resources}
    assert {
        ("role", "owner"),
        ("role", "operations_lead"),
        ("role", "verified_customer"),
        ("role", "customer"),
        ("channel", "start_here"),
        ("channel", "team_dashboard"),
        ("channel", "feature_requests"),
    } <= keys
    assert next(item for item in resources if item.resource_key == "team_dashboard").audience == "staff"
    assert next(
        item
        for item in resources
        if item.resource_type == "channel" and item.resource_key == "start_here"
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
        "product_overview",
        "how_it_works",
        "pricing",
        "workspace_guide",
        "review_queue",
        "analytics",
        "support",
        "onboarding",
        "team_dashboard",
        "feature_requests",
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
    assert hero_panels == {"welcome"}


def test_discord_saas_polish_has_exactly_seven_categories_and_nineteen_channels():
    config = load_config(Path("config/discord"))
    assert len(config["server"]["categories"]) == 7
    assert len(config["channels"]["channels"]) == 19
    assert [item["key"] for item in config["server"]["categories"]] == [
        "start_here",
        "platform",
        "workspaces",
        "customers",
        "community",
        "team",
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
        "customer-review",
        "operator-alerts",
        "ticket-logs",
    ]
    assert not any(item["category"] == "private_requests" for item in config["channels"]["channels"])


def test_legacy_cleanup_candidates_do_not_overlap_current_channel_names():
    config = load_config(Path("config/discord"))
    current_names = {
        item.name for item in plan_resources(config) if item.resource_type == "channel"
    }
    assert not LEGACY_MANAGED_CHANNEL_NAMES & current_names


def test_rules_acceptance_gates_member_areas_but_not_start_here():
    config = load_config(Path("config/discord"))
    category_audiences = {
        item["key"]: item["audience"] for item in config["server"]["categories"]
    }
    assert category_audiences["start_here"] == "public"
    assert {category_audiences[key] for key in ("platform", "workspaces", "customers", "community")} == {
        "member"
    }
    assert category_audiences["team"] == "staff"
    assert category_audiences["private_requests"] == "staff"
    for channel in config["channels"]["channels"]:
        if channel["category"] == "start_here":
            assert channel["audience"] == "public"
        elif channel["category"] not in {"team", "private_requests"}:
            assert channel["audience"] == "member"


def test_discord_premium_role_and_forum_boundaries_are_configured():
    config = load_config(Path("config/discord"))
    names = {item["key"] for item in config["roles"]["roles"]}
    assert {"member", "verified_customer", "customer", "trial_user"} <= names
    assert set(config["role_panels"]["panels"]["notifications"]) == {
        "product_updates",
        "processing_alerts",
        "feature_releases",
        "case_studies",
        "community_events",
    }
    assert set(config["role_panels"]["account_type_role_keys"]) == {
        "creator",
        "agency",
        "media_company",
        "trial_user",
    }
    assert not set(config["role_panels"]["account_type_role_keys"]) & {
        "workspace_owner",
        "reviewer",
        "publisher",
        "analytics_viewer",
        "customer",
        "verified_customer",
    }
    channels = {item["key"]: item for item in config["channels"]["channels"]}
    assert channels["feature_requests"]["tags"] == [
        "Feature", "Integration", "Workflow", "Bug", "Quality of Life", "In Review", "Planned"
    ]
    assert config["embeds"]["embeds"]["choose_roles"]["actions"] == []
    assert set(config["tickets"]["ticket_types"]) >= {"general", "bug_report", "feature_request"}
