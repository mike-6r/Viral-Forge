"""Deterministic policy evaluation and bounded unattended-operation helpers.

This module is intentionally the only place that decides whether an automatic
action may proceed.  Task and Discord layers consume its durable output; they do
not contain their own autoplay rules.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.autopilot.models import (
    AutopilotDecision,
    AutopilotException,
    AutopilotGlobalControl,
    AutopilotPolicy,
    AutopilotQueueRank,
    AutopilotRun,
    AutopilotScheduleSlot,
)
from app.brands.models import DestinationAccount
from app.content_packages.models import ContentPackage
from app.operations.models import OperatorTask
from app.production.models import PostingQueueItem, ProductionClip

AUTOMATION_LEVELS = ("MANUAL", "ASSISTED", "SUPERVISED_AUTOPILOT", "AUTOPILOT")
DECISIONS = ("ALLOW", "BLOCK", "REQUIRE_REVIEW")
ACTIONS = (
    "DISCOVER_SOURCE",
    "ACCEPT_SOURCE",
    "PROCESS_SOURCE",
    "SELECT_OPPORTUNITY",
    "RENDER_CLIP",
    "ACCEPT_RENDER",
    "CREATE_CORRECTION",
    "ACCEPT_CORRECTION",
    "GENERATE_METADATA",
    "APPROVE_METADATA",
    "SCHEDULE_CONTENT",
    "TRANSFER_DRAFT",
    "DIRECT_POST",
    "REFRESH_ANALYTICS",
)

_SUPERVISED_ACTIONS = {
    "DISCOVER_SOURCE",
    "ACCEPT_SOURCE",
    "PROCESS_SOURCE",
    "SELECT_OPPORTUNITY",
    "RENDER_CLIP",
    "ACCEPT_RENDER",
    "CREATE_CORRECTION",
    "GENERATE_METADATA",
    "SCHEDULE_CONTENT",
    "REFRESH_ANALYTICS",
}


def default_policy_config() -> dict[str, object]:
    """Safe, complete configuration. Defaults never permit external posting."""
    return {
        "general": {"enabled": False, "max_concurrent_jobs": 1, "retry_limit": 2},
        "discovery": {
            "automatic_discovery_enabled": False,
            "allowed_providers": [],
            "daily_limit": 0,
        },
        "source": {
            "automatic_source_acceptance_enabled": False,
            "minimum_trust": 0.9,
            "require_rights": True,
            "require_moderation": True,
        },
        "clip": {
            "automatic_selection_enabled": False,
            "minimum_score": 85,
            "minimum_confidence": 80,
            "allow_series": False,
        },
        "render": {
            "automatic_render_enabled": False,
            "minimum_quality": 80,
            "automatic_correction_enabled": False,
            "allowed_corrections": [],
            "max_correction_revisions": 0,
        },
        "metadata": {
            "automatic_generation_enabled": False,
            "automatic_approval_enabled": False,
            "minimum_confidence": 85,
        },
        "schedule": {
            "enabled": False,
            "timezone": "UTC",
            "posting_windows": [],
            "maximum_posts_per_day": 0,
            "minimum_spacing_minutes": 0,
            "blackout_dates": [],
            "missed_slot_behavior": "REQUIRE_REVIEW",
        },
        "publishing": {
            "automatic_transfer_enabled": False,
            "automatic_direct_post_enabled": False,
            "require_final_human_confirmation": True,
            "permitted_providers": [],
            "permitted_destination_ids": [],
            "privacy_default": "private",
            "public_posting_authorized": False,
            "emergency_pause": False,
        },
        "analytics": {
            "automatic_refresh_enabled": False,
            "refresh_interval_minutes": 60,
            "briefing_enabled": True,
        },
    }


def _merged_config(raw: dict[str, object] | None) -> dict[str, object]:
    result = default_policy_config()
    for key, value in (raw or {}).items():
        current = result.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            result[key] = {**current, **value}
        else:
            result[key] = value
    return result


def _apply_update(
    current: dict[str, object] | None, update: dict[str, object]
) -> dict[str, object]:
    """Merge an operator patch without silently resetting unrelated safeguards."""
    result = _merged_config(current)
    for key, value in update.items():
        existing = result.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            result[key] = {**existing, **value}
        else:
            result[key] = value
    return result


def policy_for(
    session: Session, brand_id: uuid.UUID, *, create: bool = False
) -> AutopilotPolicy | None:
    policy = session.scalar(select(AutopilotPolicy).where(AutopilotPolicy.brand_id == brand_id))
    if policy is None and create:
        policy = AutopilotPolicy(brand_id=brand_id, config_json=default_policy_config())
        session.add(policy)
        session.flush()
    return policy


def global_control(session: Session, *, create: bool = False) -> AutopilotGlobalControl | None:
    control = session.scalar(
        select(AutopilotGlobalControl).where(AutopilotGlobalControl.control_key == "GLOBAL")
    )
    if control is None and create:
        control = AutopilotGlobalControl(control_key="GLOBAL")
        session.add(control)
        session.flush()
    return control


def set_global_control(
    session: Session,
    actor_id: uuid.UUID,
    expected_version: int,
    *,
    emergency_stop: bool | None = None,
    discovery_paused: bool | None = None,
    processing_paused: bool | None = None,
    publishing_paused: bool | None = None,
) -> AutopilotGlobalControl:
    control = global_control(session, create=True)
    assert control is not None
    if control.version != expected_version:
        raise ValueError("Global-control version conflict.")
    for field, value in (
        ("emergency_stop", emergency_stop),
        ("discovery_paused", discovery_paused),
        ("processing_paused", processing_paused),
        ("publishing_paused", publishing_paused),
    ):
        if value is not None:
            setattr(control, field, value)
    control.version += 1
    control.updated_by_id = actor_id
    audit_brand = session.scalar(select(AutopilotPolicy.brand_id).limit(1))
    if audit_brand is not None:
        session.add(
            AuditEvent(
                actor_id=actor_id,
                entity_type="autopilot_global_control",
                entity_id=control.id,
                brand_id=audit_brand,
                event_name="autopilot.emergency_control.updated",
                payload={
                    "emergency_stop": control.emergency_stop,
                    "discovery_paused": control.discovery_paused,
                    "processing_paused": control.processing_paused,
                    "publishing_paused": control.publishing_paused,
                },
            )
        )
    session.commit()
    return control


def update_policy(
    session: Session,
    brand_id: uuid.UUID,
    actor_id: uuid.UUID,
    expected_version: int,
    config_json: dict[str, object],
    *,
    automation_level: str | None = None,
    paused: bool | None = None,
) -> AutopilotPolicy:
    policy = policy_for(session, brand_id, create=True)
    assert policy is not None
    if policy.version != expected_version:
        raise ValueError("Policy version conflict; refresh before changing automation settings.")
    level = automation_level or policy.automation_level
    if level not in AUTOMATION_LEVELS:
        raise ValueError("Unknown automation level.")
    candidate = _apply_update(policy.config_json, config_json)
    errors = validate_policy(candidate, level)
    if errors:
        raise ValueError("Unsafe autopilot policy: " + "; ".join(errors))
    policy.config_json = candidate
    policy.automation_level = level
    if paused is not None:
        policy.is_paused = paused
    policy.version += 1
    policy.updated_by_id = actor_id
    session.add(
        AuditEvent(
            actor_id=actor_id,
            brand_id=brand_id,
            entity_type="autopilot_policy",
            entity_id=policy.id,
            event_name="autopilot.policy.updated",
            payload={"version": policy.version, "level": level, "paused": policy.is_paused},
        )
    )
    session.commit()
    return policy


def validate_policy(config: dict[str, object], level: str) -> list[str]:
    if level not in AUTOMATION_LEVELS:
        return ["unknown automation level"]
    if level == "MANUAL":
        return []
    schedule = config.get("schedule", {})
    source = config.get("source", {})
    publishing = config.get("publishing", {})
    if (
        not isinstance(schedule, dict)
        or not isinstance(source, dict)
        or not isinstance(publishing, dict)
    ):
        return ["policy sections must be objects"]
    errors: list[str] = []
    try:
        ZoneInfo(str(schedule.get("timezone", "UTC")))
    except Exception:
        errors.append("invalid schedule timezone")
    if bool(source.get("automatic_source_acceptance_enabled")) and not bool(
        source.get("require_rights")
    ):
        errors.append("automatic source acceptance requires rights policy")
    if bool(source.get("automatic_source_acceptance_enabled")) and not bool(
        source.get("require_moderation")
    ):
        errors.append("automatic source acceptance requires moderation policy")
    if bool(schedule.get("enabled")) and int(schedule.get("maximum_posts_per_day", 0) or 0) <= 0:
        errors.append("scheduled operations require a daily limit")
    if bool(schedule.get("enabled")) and int(schedule.get("minimum_spacing_minutes", 0) or 0) <= 0:
        errors.append("scheduled operations require minimum spacing")
    if bool(publishing.get("automatic_direct_post_enabled")):
        errors.append(
            "Direct Post remains disabled until a provider-specific authorization validator is installed"
        )
    if bool(publishing.get("automatic_transfer_enabled")) and not bool(
        publishing.get("require_final_human_confirmation", True)
    ):
        errors.append("automatic transfer requires final human confirmation")
    return errors


def _section(config: dict[str, object], name: str) -> dict[str, object]:
    value = config.get(name, {})
    return value if isinstance(value, dict) else {}


def _number(evidence: dict[str, object], name: str) -> int | None:
    value = evidence.get(name)
    return int(value) if isinstance(value, int | float) else None


def _config_number(value: object, default: float) -> float:
    return float(value) if isinstance(value, int | float | str) else default


@dataclass(frozen=True)
class PolicyResult:
    decision: str
    record: AutopilotDecision


def decide(
    session: Session,
    brand_id: uuid.UUID,
    action: str,
    object_type: str,
    object_id: str | uuid.UUID,
    *,
    evidence: dict[str, object] | None = None,
    correlation_key: str | None = None,
) -> PolicyResult:
    """Record a deterministic, evidence-backed and fail-closed policy decision."""
    if action not in ACTIONS:
        raise ValueError("Unsupported autopilot action.")
    policy = policy_for(session, brand_id, create=True)
    assert policy is not None
    config = _merged_config(policy.config_json)
    evidence = evidence or {}
    reasons: list[str] = []
    missing: list[str] = []
    thresholds: dict[str, object] = {}
    actuals: dict[str, object] = {}
    decision = "REQUIRE_REVIEW"
    explanation = "A human decision is required by the current policy."
    level = policy.automation_level
    control = global_control(session, create=True)
    assert control is not None
    action_group_paused = (
        control.emergency_stop
        or (action in {"DISCOVER_SOURCE", "ACCEPT_SOURCE"} and control.discovery_paused)
        or (
            action
            in {
                "PROCESS_SOURCE",
                "SELECT_OPPORTUNITY",
                "RENDER_CLIP",
                "ACCEPT_RENDER",
                "CREATE_CORRECTION",
                "ACCEPT_CORRECTION",
                "GENERATE_METADATA",
                "APPROVE_METADATA",
            }
            and control.processing_paused
        )
        or (
            action in {"SCHEDULE_CONTENT", "TRANSFER_DRAFT", "DIRECT_POST"}
            and control.publishing_paused
        )
    )
    if (
        policy.is_paused
        or action_group_paused
        or bool(_section(config, "publishing").get("emergency_pause"))
    ):
        decision, reasons, explanation = (
            "BLOCK",
            ["EMERGENCY_PAUSE"],
            "Automation is paused for this brand.",
        )
    elif action == "DIRECT_POST":
        decision, reasons, explanation = (
            "BLOCK",
            ["DIRECT_POST_REQUIRES_PROVIDER_AUTHORIZATION"],
            "Direct Post is blocked until the provider-specific authorization boundary approves it.",
        )
    elif level == "MANUAL":
        reasons, explanation = ["MANUAL_MODE"], "Manual mode never advances work automatically."
    elif action not in _SUPERVISED_ACTIONS and level != "AUTOPILOT":
        reasons, explanation = (
            ["HUMAN_GATE"],
            "This action requires an operator at the selected automation level.",
        )
    else:
        decision, reasons, explanation = (
            "ALLOW",
            ["POLICY_ENABLED"],
            "The persisted policy permits this bounded automatic action.",
        )
        if action in {"ACCEPT_SOURCE", "PROCESS_SOURCE"}:
            source = _section(config, "source")
            thresholds["minimum_trust"] = source.get("minimum_trust")
            for key in ("rights_approved", "moderation_approved", "source_trust"):
                if key not in evidence:
                    missing.append(key)
            actuals["source_trust"] = evidence.get("source_trust")
            if not bool(evidence.get("rights_approved")):
                decision, reasons = "REQUIRE_REVIEW", ["RIGHTS_REVIEW_REQUIRED"]
            elif not bool(evidence.get("moderation_approved")):
                decision, reasons = "REQUIRE_REVIEW", ["MODERATION_REVIEW_REQUIRED"]
            elif (_number(evidence, "source_trust") or 0) < _config_number(
                source.get("minimum_trust"), 1
            ):
                decision, reasons = "REQUIRE_REVIEW", ["SOURCE_TRUST_BELOW_THRESHOLD"]
        elif action in {
            "SELECT_OPPORTUNITY",
            "ACCEPT_RENDER",
            "GENERATE_METADATA",
            "APPROVE_METADATA",
        }:
            section_name = (
                "clip"
                if action == "SELECT_OPPORTUNITY"
                else "render"
                if action == "ACCEPT_RENDER"
                else "metadata"
            )
            section = _section(config, section_name)
            minimum = int(
                _config_number(
                    section.get(
                        "minimum_score",
                        section.get("minimum_quality", section.get("minimum_confidence", 100)),
                    ),
                    100,
                )
            )
            value = _number(evidence, "score")
            if value is None:
                value = _number(evidence, "quality_score")
            if value is None:
                value = _number(evidence, "confidence")
            thresholds["minimum"] = minimum
            actuals["score"] = value
            if value is None:
                missing.append("quality/confidence score")
            elif value < minimum:
                decision, reasons = "REQUIRE_REVIEW", ["QUALITY_BELOW_THRESHOLD"]
        elif action == "SCHEDULE_CONTENT":
            schedule = _section(config, "schedule")
            if not bool(schedule.get("enabled")):
                decision, reasons = "REQUIRE_REVIEW", ["SCHEDULING_DISABLED"]
        elif action in {"TRANSFER_DRAFT", "DIRECT_POST"}:
            publish = _section(config, "publishing")
            if action == "DIRECT_POST":
                decision, reasons = "BLOCK", ["DIRECT_POST_REQUIRES_PROVIDER_AUTHORIZATION"]
            elif not bool(publish.get("automatic_transfer_enabled")):
                decision, reasons = "REQUIRE_REVIEW", ["TRANSFER_REQUIRES_HUMAN_CONFIRMATION"]
        if missing:
            decision, reasons = "REQUIRE_REVIEW", ["MISSING_EVIDENCE"]
            explanation = "Evidence required for an automatic decision is missing."
    record = AutopilotDecision(
        brand_id=brand_id,
        policy_id=policy.id,
        policy_version=policy.version,
        action=action,
        decision=decision,
        object_type=object_type,
        object_id=str(object_id),
        reason_codes=reasons,
        explanation=explanation,
        evidence_json=evidence,
        thresholds_json=thresholds,
        actuals_json=actuals,
        missing_evidence=missing,
        confidence=_number(evidence, "confidence"),
        correlation_key=correlation_key,
    )
    session.add(record)
    session.flush()
    session.add(
        AuditEvent(
            brand_id=brand_id,
            entity_type="autopilot_decision",
            entity_id=record.id,
            event_name="autopilot.decision.recorded",
            payload={
                "action": action,
                "decision": decision,
                "reason_codes": reasons,
                "policy_version": policy.version,
            },
        )
    )
    if decision != "ALLOW":
        open_exception(
            session,
            brand_id,
            record,
            object_type,
            str(object_id),
            reasons[0] if reasons else "REVIEW_REQUIRED",
            explanation,
        )
    session.commit()
    return PolicyResult(decision, record)


def open_exception(
    session: Session,
    brand_id: uuid.UUID,
    decision: AutopilotDecision | None,
    object_type: str,
    object_id: str,
    category: str,
    reason: str,
    *,
    severity: str = "WARNING",
    recovery: str = "OPERATOR_REQUIRED",
) -> AutopilotException:
    key = f"{category}:{object_type}:{object_id}"
    item = session.scalar(
        select(AutopilotException).where(
            AutopilotException.brand_id == brand_id,
            AutopilotException.dedupe_key == key,
            AutopilotException.status == "OPEN",
        )
    )
    if item is None:
        item = AutopilotException(
            brand_id=brand_id,
            decision_id=decision.id if decision else None,
            category=category,
            severity=severity,
            dedupe_key=key,
            object_type=object_type,
            object_id=object_id,
            reason=reason,
            recommended_action="Review and resolve in Operations",
            retry_state=recovery,
        )
        session.add(item)
        task = OperatorTask(
            brand_id=brand_id,
            priority="HIGH" if severity in {"ERROR", "CRITICAL"} else "NORMAL",
            task_type=f"AUTOPILOT_{category}",
            dedupe_key=f"autopilot:{key}",
            title="Autopilot needs a decision",
            reason=reason,
            action_label="Review Exceptions",
            payload_json={"exception_key": key},
        )
        session.add(task)
    else:
        item.occurrences += 1
        item.reason = reason
    session.flush()
    return item


def rank_queue(session: Session, brand_id: uuid.UUID) -> list[AutopilotQueueRank]:
    """Rank existing queue records only; it never creates a second posting queue."""
    items = list(
        session.scalars(
            select(PostingQueueItem)
            .where(
                PostingQueueItem.brand_id == brand_id, PostingQueueItem.status == "READY_TO_POST"
            )
            .order_by(PostingQueueItem.created_at)
            .limit(100)
        )
    )
    ranked: list[tuple[PostingQueueItem, int, dict[str, object]]] = []
    for item in items:
        clip = session.get(ProductionClip, item.clip_id)
        score = 50 + (20 if clip and clip.approval_status == "APPROVED" else -50)
        evidence: dict[str, object] = {
            "clip_approved": bool(clip and clip.approval_status == "APPROVED"),
            "queue_age_hours": int((datetime.now(UTC) - item.created_at).total_seconds() // 3600),
        }
        ranked.append((item, score, evidence))
    ranked.sort(key=lambda entry: (-entry[1], str(entry[0].created_at)))
    results: list[AutopilotQueueRank] = []
    for position, (item, score, evidence) in enumerate(ranked, 1):
        row = session.scalar(
            select(AutopilotQueueRank).where(AutopilotQueueRank.queue_item_id == item.id)
        )
        if row is None:
            row = AutopilotQueueRank(
                brand_id=brand_id,
                queue_item_id=item.id,
                rank_score=score,
                explanation="Approved clips rank ahead of items without a completed human review.",
                evidence_json=evidence,
            )
            session.add(row)
        row.rank_score, row.rank_position, row.evidence_json = score, position, evidence
        results.append(row)
    session.commit()
    return results


def reserve_slot(
    session: Session,
    brand_id: uuid.UUID,
    queue_item_id: uuid.UUID,
    destination_id: uuid.UUID,
    package_id: uuid.UUID,
    scheduled_for: str,
) -> AutopilotScheduleSlot:
    item = session.get(PostingQueueItem, queue_item_id)
    destination = session.get(DestinationAccount, destination_id)
    package = session.get(ContentPackage, package_id)
    if (
        item is None
        or destination is None
        or package is None
        or item.brand_id != brand_id
        or destination.brand_id != brand_id
        or package.brand_id != brand_id
        or package.clip_id != item.clip_id
    ):
        raise ValueError(
            "Clip, package, queue item, and destination must belong to the same brand."
        )
    result = decide(
        session,
        brand_id,
        "SCHEDULE_CONTENT",
        "posting_queue_item",
        item.id,
        evidence={"destination_id": str(destination_id)},
    )
    if result.decision != "ALLOW":
        raise ValueError("Policy requires review before scheduling this content.")
    existing = session.scalar(
        select(AutopilotScheduleSlot).where(AutopilotScheduleSlot.queue_item_id == item.id)
    )
    if existing is not None:
        return existing
    policy = policy_for(session, brand_id, create=True)
    assert policy is not None
    slot = AutopilotScheduleSlot(
        brand_id=brand_id,
        destination_account_id=destination_id,
        queue_item_id=item.id,
        clip_id=item.clip_id,
        content_package_id=package.id,
        content_package_generation_version=package.generation_version,
        policy_version=policy.version,
        scheduled_for=scheduled_for,
    )
    session.add(slot)
    session.flush()
    session.add(
        AuditEvent(
            brand_id=brand_id,
            entity_type="autopilot_schedule_slot",
            entity_id=slot.id,
            event_name="autopilot.schedule.reserved",
            payload={"queue_item_id": str(item.id), "provider_mode": slot.provider_mode},
        )
    )
    session.commit()
    return slot


def stale_runs(session: Session, older_than_minutes: int = 30) -> list[AutopilotRun]:
    cutoff = (datetime.now(UTC) - timedelta(minutes=older_than_minutes)).isoformat()
    rows = list(
        session.scalars(
            select(AutopilotRun)
            .where(
                AutopilotRun.status == "RUNNING",
                AutopilotRun.heartbeat_at.is_not(None),
                AutopilotRun.heartbeat_at < cutoff,
            )
            .limit(100)
        )
    )
    for row in rows:
        row.status, row.recovery_class = "STALE", "OPERATOR_REQUIRED"
        open_exception(
            session,
            row.brand_id,
            None,
            row.object_type,
            row.object_id,
            "STALE_JOB",
            "A background job lost its heartbeat; review before retrying.",
            severity="ERROR",
        )
    session.commit()
    return rows


def automation_summary(session: Session, brand_id: uuid.UUID) -> dict[str, object]:
    policy = policy_for(session, brand_id, create=True)
    assert policy is not None
    return {
        "level": policy.automation_level,
        "version": policy.version,
        "paused": policy.is_paused,
        "exceptions": int(
            session.scalar(
                select(func.count())
                .select_from(AutopilotException)
                .where(AutopilotException.brand_id == brand_id, AutopilotException.status == "OPEN")
            )
            or 0
        ),
        "scheduled": int(
            session.scalar(
                select(func.count())
                .select_from(AutopilotScheduleSlot)
                .where(
                    AutopilotScheduleSlot.brand_id == brand_id,
                    AutopilotScheduleSlot.status == "RESERVED",
                )
            )
            or 0
        ),
    }
