"""Safe, database-backed business operations for the Discord control plane.

This module deliberately stores identifiers and redacted evidence only.  It never
stores Discord message bodies, credentials, attachment contents, or tokens.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.discord_business.models import (
    DiscordAggregateSnapshot,
    DiscordAnnouncement,
    DiscordAppeal,
    DiscordCustomerLink,
    DiscordGuildConfig,
    DiscordIncident,
    DiscordModerationCase,
    DiscordRoleGrant,
    DiscordRulesAcceptance,
    DiscordStaffAvailability,
    DiscordStaffNote,
    DiscordTicket,
    DiscordTicketNote,
)

TICKET_TRANSITIONS = {
    "NEW": {"OPEN", "SPAM", "DUPLICATE", "CLOSED"},
    "OPEN": {"WAITING_FOR_STAFF", "WAITING_FOR_CUSTOMER", "ESCALATED", "RESOLVED", "CLOSED", "SPAM", "DUPLICATE"},
    "WAITING_FOR_STAFF": {"OPEN", "ESCALATED", "RESOLVED", "CLOSED"},
    "WAITING_FOR_CUSTOMER": {"OPEN", "RESOLVED", "CLOSED"},
    "ESCALATED": {"OPEN", "WAITING_FOR_CUSTOMER", "RESOLVED", "CLOSED"},
    "RESOLVED": {"REOPENED", "CLOSED"},
    "REOPENED": {"OPEN", "WAITING_FOR_STAFF", "ESCALATED", "RESOLVED", "CLOSED"},
    "CLOSED": {"REOPENED"},
    "SPAM": {"CLOSED"},
    "DUPLICATE": {"CLOSED"},
}

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("discord_token", re.compile(r"(?:mfa\.)?[A-Za-z\d_-]{24,}\.[A-Za-z\d_-]{6,}\.[A-Za-z\d_-]{20,}")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("database_url", re.compile(r"(?:postgres(?:ql)?|mysql)://[^\s:@/]+:[^\s@/]+@", re.I)),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z\d._~+/-]{20,}", re.I)),
    ("api_key", re.compile(r"\b(?:sk|pk|AIza)[-_A-Za-z\d]{20,}\b")),
    ("webhook_url", re.compile(r"https://(?:discord(?:app)?\.com)/api/webhooks/\d+/[\w-]+", re.I)),
)


@dataclass(frozen=True)
class ModerationFinding:
    rule_key: str
    action: str
    evidence: dict[str, object]


def scan_message(content: str, *, mention_count: int = 0, repeated: bool = False) -> ModerationFinding | None:
    """Return only a redacted category/fingerprint; never return the matched value."""
    for category, pattern in SECRET_PATTERNS:
        match = pattern.search(content)
        if match:
            return ModerationFinding(
                "secret_pattern",
                "DELETE_AND_REVIEW",
                {"category": category, "length": len(match.group(0)), "redacted": True},
            )
    if re.search(r"(?:discord\.gg|discord(?:app)?\.com/invite)/[A-Za-z0-9-]+", content, re.I):
        return ModerationFinding("discord_invite", "DELETE_AND_REVIEW", {"redacted": False})
    if mention_count > 5:
        return ModerationFinding("excessive_mentions", "WARN_AND_REVIEW", {"mentions": mention_count})
    if repeated:
        return ModerationFinding("repeated_message", "REVIEW", {"redacted": False})
    return None


class OperationsError(Exception):
    pass


class OperationsRepository:
    def _config(self, session: Session, guild_id: int) -> DiscordGuildConfig:
        row = session.scalar(select(DiscordGuildConfig).where(DiscordGuildConfig.guild_id == str(guild_id)))
        if row is None:
            raise OperationsError("Discord server setup has not been applied yet.")
        return row

    def ticket_sla(self, ticket: DiscordTicket, config: dict[str, Any]) -> None:
        settings = config["ticket_sla"]
        priority = settings["priorities"].get(ticket.priority.lower(), settings["priorities"]["normal"])
        multiplier = float(priority["multiplier"])
        now = datetime.now(UTC)
        defaults = settings["default"]
        ticket.sla_first_response_due_at = now + timedelta(
            minutes=int(float(defaults["first_response_minutes"]) * multiplier)
        )
        ticket.sla_resolution_due_at = now + timedelta(
            minutes=int(float(defaults["resolution_minutes"]) * multiplier)
        )

    def prepare_ticket(self, ticket: DiscordTicket, config: dict[str, Any]) -> DiscordTicket:
        ticket.status = "NEW"
        ticket.department = ticket.ticket_type
        self.ticket_sla(ticket, config)
        return ticket

    def transition_ticket(
        self, session: Session, ticket: DiscordTicket, status: str, actor_id: int, reason: str | None = None
    ) -> DiscordTicket:
        session.flush()
        target = status.upper()
        if target not in TICKET_TRANSITIONS.get(ticket.status, set()):
            raise OperationsError(f"Ticket cannot transition from {ticket.status} to {target}.")
        ticket.status = target
        now = datetime.now(UTC)
        if target == "RESOLVED":
            ticket.resolved_at = now
        if target == "CLOSED":
            ticket.closed_at = now
        if target == "ESCALATED":
            ticket.escalation_reason = reason or "Staff escalation"
        session.add(
            DiscordTicketNote(
                ticket_id=ticket.id,
                author_discord_user_id=str(actor_id),
                visibility="STAFF_ONLY",
                category="STATUS",
                content=f"Status changed to {target}" + (f": {reason}" if reason else ""),
            )
        )
        return ticket

    def assign_ticket(self, session: Session, ticket: DiscordTicket, staff_id: int, actor_id: int) -> DiscordTicket:
        session.flush()
        ticket.assigned_staff_discord_user_id = str(staff_id)
        ticket.status = "WAITING_FOR_STAFF" if ticket.status == "NEW" else ticket.status
        session.add(DiscordTicketNote(ticket_id=ticket.id, author_discord_user_id=str(actor_id), visibility="STAFF_ONLY", category="ASSIGNMENT", content=f"Assigned to staff member {staff_id}."))
        return ticket

    def add_ticket_note(
        self, session: Session, ticket: DiscordTicket, author_id: int, content: str, *, visibility: str = "STAFF_ONLY"
    ) -> DiscordTicketNote:
        session.flush()
        if visibility not in {"STAFF_ONLY", "CUSTOMER_VISIBLE", "OPERATOR_ONLY"}:
            raise OperationsError("Invalid note visibility.")
        note = DiscordTicketNote(ticket_id=ticket.id, author_discord_user_id=str(author_id), visibility=visibility, content=content)
        if visibility == "CUSTOMER_VISIBLE":
            now = datetime.now(UTC)
            ticket.first_response_at = ticket.first_response_at or now
            ticket.last_staff_response_at = now
            ticket.status = "WAITING_FOR_CUSTOMER"
        session.add(note)
        return note

    def record_customer_reply(self, ticket: DiscordTicket) -> None:
        ticket.last_customer_response_at = datetime.now(UTC)
        if ticket.status in {"WAITING_FOR_CUSTOMER", "RESOLVED", "CLOSED"}:
            ticket.status = "REOPENED" if ticket.status in {"RESOLVED", "CLOSED"} else "OPEN"

    def overdue_tickets(self, session: Session, guild_id: int) -> list[DiscordTicket]:
        config = self._config(session, guild_id)
        now = datetime.now(UTC)
        return list(session.scalars(select(DiscordTicket).where(DiscordTicket.guild_config_id == config.id, DiscordTicket.status.not_in(["CLOSED", "SPAM", "DUPLICATE"]), DiscordTicket.sla_resolution_due_at.is_not(None), DiscordTicket.sla_resolution_due_at < now).order_by(DiscordTicket.sla_resolution_due_at)))

    def create_case(
        self, session: Session, guild_id: int, subject_id: int, finding: ModerationFinding, *, moderator_id: int | None = None
    ) -> DiscordModerationCase:
        config = self._config(session, guild_id)
        number = (session.scalar(select(func.max(DiscordModerationCase.case_number)).where(DiscordModerationCase.guild_config_id == config.id)) or 0) + 1
        case = DiscordModerationCase(guild_config_id=config.id, case_number=number, subject_discord_user_id=str(subject_id), moderator_discord_user_id=str(moderator_id) if moderator_id else None, origin="AUTOMATIC" if moderator_id is None else "MANUAL", rule_key=finding.rule_key, action=finding.action, reason=f"Triggered {finding.rule_key}", evidence_redacted=finding.evidence)
        session.add(case)
        return case

    def appeal_case(self, session: Session, case: DiscordModerationCase, appellant_id: int, explanation: str, outcome: str | None) -> DiscordAppeal:
        if case.subject_discord_user_id != str(appellant_id):
            raise OperationsError("Only the affected member may appeal this case.")
        if case.appeal_status in {"OPEN", "DECIDED"}:
            raise OperationsError("This case already has an appeal.")
        appeal = DiscordAppeal(moderation_case_id=case.id, appellant_discord_user_id=str(appellant_id), explanation=explanation, requested_outcome=outcome)
        case.appeal_status = "OPEN"
        session.add(appeal)
        return appeal

    def grant_role(
        self, session: Session, guild_id: int, user_id: int, role_key: str, source: str, *, actor_id: int | None = None, expires_at: datetime | None = None, reason: str | None = None
    ) -> DiscordRoleGrant:
        config = self._config(session, guild_id)
        grant = session.scalar(select(DiscordRoleGrant).where(DiscordRoleGrant.guild_config_id == config.id, DiscordRoleGrant.discord_user_id == str(user_id), DiscordRoleGrant.role_key == role_key, DiscordRoleGrant.source == source))
        if grant is None:
            grant = DiscordRoleGrant(guild_config_id=config.id, discord_user_id=str(user_id), role_key=role_key, source=source)
            session.add(grant)
        grant.assigned_by_discord_user_id = str(actor_id) if actor_id else None
        grant.expires_at, grant.reason, grant.removed_at, grant.removal_reason = expires_at, reason, None, None
        return grant

    def expire_role_grants(self, session: Session, guild_id: int, now: datetime | None = None) -> list[DiscordRoleGrant]:
        config, now = self._config(session, guild_id), now or datetime.now(UTC)
        grants = list(session.scalars(select(DiscordRoleGrant).where(DiscordRoleGrant.guild_config_id == config.id, DiscordRoleGrant.expires_at.is_not(None), DiscordRoleGrant.expires_at <= now, DiscordRoleGrant.removed_at.is_(None))))
        for grant in grants:
            grant.removed_at, grant.removal_reason = now, "expired"
        return grants

    def set_availability(self, session: Session, guild_id: int, user_id: int, status: str, note: str | None) -> DiscordStaffAvailability:
        if status not in {"AVAILABLE", "BUSY", "AWAY", "OFF_DUTY"}:
            raise OperationsError("Invalid availability state.")
        config = self._config(session, guild_id)
        row = session.scalar(select(DiscordStaffAvailability).where(DiscordStaffAvailability.guild_config_id == config.id, DiscordStaffAvailability.discord_user_id == str(user_id)))
        if row is None:
            row = DiscordStaffAvailability(guild_config_id=config.id, discord_user_id=str(user_id))
            session.add(row)
        row.status, row.note = status, note
        return row

    def create_incident(self, session: Session, guild_id: int, title: str, severity: str, owner_id: int, systems: list[str]) -> DiscordIncident:
        if severity not in {"INFORMATIONAL", "MINOR", "MAJOR", "CRITICAL"}:
            raise OperationsError("Invalid incident severity.")
        config = self._config(session, guild_id)
        number = (session.scalar(select(func.max(DiscordIncident.incident_number)).where(DiscordIncident.guild_config_id == config.id)) or 0) + 1
        row = DiscordIncident(guild_config_id=config.id, incident_number=number, title=title, severity=severity, owner_discord_user_id=str(owner_id), affected_systems=systems)
        session.add(row)
        return row

    def create_announcement(self, session: Session, guild_id: int, author_id: int, title: str, body: str, target_channel_key: str, notification_role_key: str | None, scheduled_for: datetime | None = None) -> DiscordAnnouncement:
        config = self._config(session, guild_id)
        row = DiscordAnnouncement(guild_config_id=config.id, author_discord_user_id=str(author_id), title=title, body=body, target_channel_key=target_channel_key, notification_role_key=notification_role_key, status="SCHEDULED" if scheduled_for else "DRAFT", scheduled_for=scheduled_for)
        session.add(row)
        return row

    def dashboard(self, session: Session, guild_id: int, member_count: int = 0) -> dict[str, dict[str, object]]:
        config = self._config(session, guild_id)
        def count(statement: Any) -> int:
            return int(session.scalar(statement) or 0)
        ticket_base = DiscordTicket.guild_config_id == config.id
        now = datetime.now(UTC)
        return {
            "server": {"members": member_count, "verified_members": count(select(func.count()).select_from(DiscordRulesAcceptance).where(DiscordRulesAcceptance.guild_config_id == config.id)), "customers": count(select(func.count()).select_from(DiscordCustomerLink).where(DiscordCustomerLink.guild_config_id == config.id))},
            "support": {"open": count(select(func.count()).select_from(DiscordTicket).where(ticket_base, DiscordTicket.status.not_in(["CLOSED", "SPAM", "DUPLICATE"]))), "unassigned": count(select(func.count()).select_from(DiscordTicket).where(ticket_base, DiscordTicket.assigned_staff_discord_user_id.is_(None), DiscordTicket.status.not_in(["CLOSED", "SPAM", "DUPLICATE"]))), "overdue": count(select(func.count()).select_from(DiscordTicket).where(ticket_base, DiscordTicket.sla_resolution_due_at < now, DiscordTicket.status.not_in(["CLOSED", "SPAM", "DUPLICATE"])))},
            "moderation": {"unresolved_cases": count(select(func.count()).select_from(DiscordModerationCase).where(DiscordModerationCase.guild_config_id == config.id, DiscordModerationCase.status == "OPEN"))},
            "incidents": {"open": count(select(func.count()).select_from(DiscordIncident).where(DiscordIncident.guild_config_id == config.id, DiscordIncident.internal_status != "RESOLVED"))},
        }

    def snapshot(self, session: Session, guild_id: int, member_count: int) -> DiscordAggregateSnapshot:
        config = self._config(session, guild_id)
        stamp = date.today().isoformat()
        row = session.scalar(select(DiscordAggregateSnapshot).where(DiscordAggregateSnapshot.guild_config_id == config.id, DiscordAggregateSnapshot.snapshot_date == stamp))
        metrics: dict[str, object] = {"sections": self.dashboard(session, guild_id, member_count)}
        if row is None:
            row = DiscordAggregateSnapshot(guild_config_id=config.id, snapshot_date=stamp, metrics=metrics)
            session.add(row)
        else:
            row.metrics = metrics
        return row

    def add_staff_note(self, session: Session, guild_id: int, subject_type: str, subject_id: str, author_id: int, content: str) -> DiscordStaffNote:
        config = self._config(session, guild_id)
        row = DiscordStaffNote(guild_config_id=config.id, subject_type=subject_type, subject_id=subject_id, author_discord_user_id=str(author_id), content=content)
        session.add(row)
        return row
