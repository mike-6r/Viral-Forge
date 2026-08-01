from pathlib import Path

from sqlalchemy import inspect

from app.discord_business.models import DiscordGuildConfig
from app.discord_business.service import BusinessRepository, load_config, plan_resources


def test_discord_business_configuration_is_complete_and_plans_private_control_plane():
    config = load_config(Path("config/discord"))
    resources = plan_resources(config)
    keys = {(item.resource_type, item.resource_key) for item in resources}
    assert {
        ("role", "owner"),
        ("role", "operator"),
        ("channel", "rules"),
        ("channel", "ops_dashboard"),
        ("channel", "bodycams_daily_hq"),
    } <= keys
    assert (
        next(item for item in resources if item.resource_key == "ops_dashboard").audience
        == "operator"
    )
    assert next(item for item in resources if item.resource_key == "rules").read_only


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
