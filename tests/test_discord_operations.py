from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect

from app.discord_business.models import DiscordModerationCase
from app.discord_business.operations import OperationsError, OperationsRepository, scan_message
from app.discord_business.service import BusinessRepository, load_config


def test_operations_models_are_registered(session):  # type: ignore[no-untyped-def]
    names = set(inspect(session.bind).get_table_names())
    assert {
        "discord_ticket_notes", "discord_moderation_cases", "discord_appeals", "discord_role_grants",
        "discord_role_sync_states", "discord_staff_availability", "discord_announcements",
        "discord_incidents", "discord_aggregate_snapshots", "discord_staff_notes",
    } <= names


def test_ticket_lifecycle_sla_and_private_note_boundary(session):  # type: ignore[no-untyped-def]
    config = BusinessRepository().guild_config(session, 77, "Operations Test", "2.1")
    ticket = BusinessRepository().open_ticket(session, config, 991, 123, "technical", "normal")
    operations = OperationsRepository()
    operations.prepare_ticket(ticket, load_config())
    assert ticket.status == "NEW"
    assert ticket.sla_first_response_due_at is not None
    operations.transition_ticket(session, ticket, "OPEN", 999)
    note = operations.add_ticket_note(session, ticket, 999, "Internal routing context")
    reply = operations.add_ticket_note(session, ticket, 999, "Customer-safe response", visibility="CUSTOMER_VISIBLE")
    session.commit()
    assert note.visibility == "STAFF_ONLY"
    assert reply.visibility == "CUSTOMER_VISIBLE"
    assert ticket.first_response_at is not None
    assert ticket.status == "WAITING_FOR_CUSTOMER"
    with pytest.raises(OperationsError, match="cannot transition"):
        operations.transition_ticket(session, ticket, "SPAM", 999)


def test_overdue_cases_appeals_grants_and_snapshot_are_durable(session):  # type: ignore[no-untyped-def]
    config = BusinessRepository().guild_config(session, 77, "Operations Test", "2.1")
    ticket = BusinessRepository().open_ticket(session, config, 992, 321, "technical", "high")
    ticket.status = "OPEN"
    ticket.sla_resolution_due_at = datetime.now(UTC) - timedelta(minutes=1)
    operations = OperationsRepository()
    finding = scan_message("example token: Bearer fake_token_value_abcdefghijklmnopqrstuvwxyz")
    assert finding is not None and finding.rule_key == "secret_pattern"
    assert "fake_token_value" not in str(finding.evidence)
    case = operations.create_case(session, 77, 321, finding)
    session.flush()
    appeal = operations.appeal_case(session, case, 321, "This was a controlled test.", "dismiss")
    grant = operations.grant_role(session, 77, 321, "trial_user", "TEST", expires_at=datetime.now(UTC) - timedelta(seconds=1))
    operations.set_availability(session, 77, 999, "AVAILABLE", "Testing")
    incident = operations.create_incident(session, 77, "Test incident", "MINOR", 999, ["api"])
    session.commit()
    assert operations.overdue_tickets(session, 77) == [ticket]
    assert appeal.status == "OPEN"
    assert operations.expire_role_grants(session, 77) == [grant]
    snapshot = operations.snapshot(session, 77, 4)
    session.commit()
    assert incident.incident_number == 1
    assert snapshot.metrics["sections"]
    assert session.query(DiscordModerationCase).count() == 1


def test_scanner_redacts_realistic_secret_patterns_and_avoids_normal_text():
    finding = scan_message("-----BEGIN PRIVATE KEY-----\nnot a real credential")
    assert finding is not None
    assert finding.evidence == {"category": "private_key", "length": 27, "redacted": True}
    assert scan_message("Here is a normal support question about a video.") is None
