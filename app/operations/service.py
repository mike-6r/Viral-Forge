"""Brand-scoped, approval-first operational intelligence.

This module only observes persisted work and creates deduplicated operator work.
It deliberately does not create publish requests or submit content to providers.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.brands.models import Brand, ContentProfile, DestinationAccount
from app.content_packages.models import ContentPackage, ContentPackageStatus
from app.discovery.models import DiscoveredMedia, DiscoveryStatus
from app.operations.models import OperationsAlert, OperationsReport, OperatorTask
from app.opportunities.models import ClipOpportunity, OpportunityReviewStatus
from app.production.models import PostingQueueItem, ProductionClip, ProductionProject


def now() -> datetime:
    return datetime.now(UTC)


def _count(session: Session, statement: Any) -> int:
    return int(session.scalar(statement) or 0)


def _integer(value: object, default: int) -> int:
    return int(value) if isinstance(value, int | str) else default


def _object_list(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


@dataclass(frozen=True)
class ReviewInboxItem:
    """One operator decision that `/viralforge review` can actually open.

    Queue-ready posts are intentionally absent: they are already approved
    creative work awaiting a separate, explicit publishing decision.
    """

    kind: str
    id: uuid.UUID


def _review_inbox_queries(brand_id: uuid.UUID) -> list[tuple[str, Select[tuple[uuid.UUID]]]]:
    """Keep review predicates in one place for Operations and Discord.

    The order is the operator workflow order and is also the priority used by
    the Discord review command.
    """
    return [
        (
            "SOURCE",
            select(ProductionProject.id)
            .where(
                ProductionProject.brand_id == brand_id,
                ProductionProject.status == "SOURCE_REVIEW_REQUIRED",
            )
            .order_by(ProductionProject.created_at),
        ),
        (
            "OPPORTUNITY",
            select(ClipOpportunity.id)
            .where(
                ClipOpportunity.brand_id == brand_id,
                ClipOpportunity.review_status == OpportunityReviewStatus.PENDING,
            )
            .order_by(ClipOpportunity.created_at),
        ),
        (
            "CLIP",
            select(ProductionClip.id)
            .where(
                ProductionClip.brand_id == brand_id,
                ProductionClip.render_status == "SUCCEEDED",
                ProductionClip.approval_status == "PENDING",
            )
            .order_by(ProductionClip.created_at),
        ),
        (
            "CONTENT_PACKAGE",
            select(ContentPackage.id)
            .where(
                ContentPackage.brand_id == brand_id,
                ContentPackage.status == ContentPackageStatus.PENDING,
            )
            .order_by(ContentPackage.created_at),
        ),
        (
            "DISCOVERY",
            select(DiscoveredMedia.id)
            .where(
                DiscoveredMedia.brand_id == brand_id,
                DiscoveredMedia.lifecycle_status == DiscoveryStatus.NEEDS_REVIEW,
            )
            .order_by(DiscoveredMedia.discovered_at),
        ),
    ]


def next_review_item(session: Session, brand_id: uuid.UUID) -> ReviewInboxItem | None:
    """Return the same next decision that Operations counts as reviewable."""
    for kind, statement in _review_inbox_queries(brand_id):
        item_id = session.scalar(statement.limit(1))
        if item_id is not None:
            return ReviewInboxItem(kind=kind, id=item_id)
    return None


def review_inbox_counts(session: Session, brand_id: uuid.UUID) -> dict[str, int]:
    """Return brand-scoped counts for items the review command can open."""
    counts: dict[str, int] = {}
    for kind, statement in _review_inbox_queries(brand_id):
        total = session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        counts[kind.lower()] = int(total or 0)
    counts["total"] = sum(counts.values())
    return counts


def schedule_for(session: Session, brand_id: uuid.UUID) -> dict[str, object]:
    """Return documented defaults merged with the brand's safe JSON schedule."""
    profile = session.scalar(select(ContentProfile).where(ContentProfile.brand_id == brand_id))
    raw = (profile.operations_schedule_json if profile else {}) or {}
    return {
        "timezone": str(raw.get("timezone") or (profile.timezone if profile else "UTC")),
        "quiet_hours": _object_list(raw.get("quiet_hours")),
        "holidays": _object_list(raw.get("holidays")),
        "pause_windows": _object_list(raw.get("pause_windows")),
        "discovery_interval_minutes": _integer(raw.get("discovery_interval_minutes"), 240),
        "processing_interval_minutes": _integer(raw.get("processing_interval_minutes"), 15),
        "review_reminder_minutes": _integer(raw.get("review_reminder_minutes"), 240),
        "morning_briefing_hour": _integer(raw.get("morning_briefing_hour"), 9),
        "evening_report_hour": _integer(raw.get("evening_report_hour"), 18),
        "publishing_windows": _object_list(raw.get("publishing_windows")),
        "maintenance_windows": _object_list(raw.get("maintenance_windows")),
    }


def is_quiet_or_paused(config: dict[str, object], at: datetime) -> bool:
    """Evaluate local quiet hours and ISO pause dates without assuming a locale."""
    timezone = str(config["timezone"])
    try:
        local = at.astimezone(ZoneInfo(timezone))
    except Exception:
        local = at
    holidays = {str(value) for value in _object_list(config["holidays"])}
    pauses = {str(value) for value in _object_list(config["pause_windows"])}
    if local.date().isoformat() in holidays:
        return True
    if local.date().isoformat() in pauses:
        return True
    for window in _object_list(config["quiet_hours"]):
        if not isinstance(window, dict):
            continue
        try:
            start = time.fromisoformat(str(window["start"]))
            end = time.fromisoformat(str(window["end"]))
        except (KeyError, TypeError, ValueError):
            continue
        current = local.timetz().replace(tzinfo=None)
        if (start <= end and start <= current < end) or (start > end and (current >= start or current < end)):
            return True
    return False


def queue_metrics(session: Session, brand_id: uuid.UUID) -> dict[str, object]:
    ready = _count(
        session,
        select(func.count())
        .select_from(PostingQueueItem)
        .where(PostingQueueItem.brand_id == brand_id, PostingQueueItem.status == "READY_TO_POST"),
    )
    failed = _count(
        session,
        select(func.count())
        .select_from(ProductionProject)
        .where(ProductionProject.brand_id == brand_id, ProductionProject.status.like("%FAILED%")),
    )
    oldest = session.scalar(
        select(func.min(PostingQueueItem.created_at)).where(
            PostingQueueItem.brand_id == brand_id,
            PostingQueueItem.status == "READY_TO_POST",
        )
    )
    age_minutes = int((now() - oldest).total_seconds() / 60) if oldest else 0
    health = "Healthy" if not failed and age_minutes < 1440 else "Attention Needed"
    if age_minutes >= 4320 or failed >= 3:
        health = "Degraded"
    return {
        "ready": ready,
        "failed": failed,
        "oldest_wait_minutes": age_minutes,
        "average_wait_minutes": age_minutes if ready == 1 else None,
        "health": health,
        "recommendation": "Queue healthy."
        if health == "Healthy"
        else "Review stalled or failed work before adding more processing.",
    }


def health_summary(session: Session, brand_id: uuid.UUID) -> dict[str, object]:
    queue = queue_metrics(session, brand_id)
    review = review_inbox_counts(session, brand_id)
    destinations = _count(
        session,
        select(func.count())
        .select_from(DestinationAccount)
        .where(DestinationAccount.brand_id == brand_id, DestinationAccount.is_active),
    )
    score = 100
    score -= min(40, _integer(queue["failed"], 0) * 20)
    score -= min(25, _integer(queue["oldest_wait_minutes"], 0) // 120)
    score -= 10 if not destinations else 0
    state = "Healthy" if score >= 85 else "Attention Needed" if score >= 65 else "Degraded" if score >= 40 else "Critical"
    return {
        "score": max(0, score),
        "state": state,
        "queue": queue,
        "review": review,
        "review_items": review["total"],
        "connected_destinations": destinations,
    }


def briefing(session: Session, brand_id: uuid.UUID, at: datetime | None = None) -> dict[str, object]:
    at = at or now()
    since = at - timedelta(days=1)
    found = _count(session, select(func.count()).select_from(DiscoveredMedia).where(DiscoveredMedia.brand_id == brand_id, DiscoveredMedia.created_at >= since))
    rendered = _count(session, select(func.count()).select_from(ProductionClip).where(ProductionClip.brand_id == brand_id, ProductionClip.render_status == "SUCCEEDED", ProductionClip.created_at >= since))
    ready = _count(session, select(func.count()).select_from(ContentPackage).where(ContentPackage.brand_id == brand_id, ContentPackage.status == "APPROVED", ContentPackage.created_at >= since))
    health = health_summary(session, brand_id)
    tasks = list(
        session.scalars(
            select(OperatorTask)
            .where(OperatorTask.brand_id == brand_id, OperatorTask.status == "OPEN")
            .order_by(OperatorTask.priority.desc())
            .limit(3)
        )
    )
    # A scheduled refresh closes stale tasks. This guard also prevents a
    # historical task from being shown as current between a decision and the
    # next scheduler tick.
    if not health["review_items"]:
        tasks = [task for task in tasks if task.task_type != "REVIEW_CONTENT"]
    return {
        "period_start": since.isoformat(),
        "videos_found": found,
        "rendered": rendered,
        "content_ready": ready,
        "health": health,
        "attention": tasks,
    }


def evening_report(session: Session, brand_id: uuid.UUID, at: datetime | None = None) -> dict[str, object]:
    report = briefing(session, brand_id, at)
    report["queue"] = queue_metrics(session, brand_id)
    queue = cast(dict[str, object], report["queue"])
    report["recommendation"] = queue["recommendation"]
    return report


def _json_report(report: dict[str, object]) -> dict[str, object]:
    """Convert report objects to a durable, credential-free JSON payload."""
    attention = _object_list(report.get("attention"))
    return {
        **report,
        "attention": [
            {"title": item.title, "priority": item.priority, "reason": item.reason}
            for item in attention
            if isinstance(item, OperatorTask)
        ],
    }


def create_due_reports(session: Session, brand_id: uuid.UUID, at: datetime) -> int:
    """Create at most one briefing and one evening report for the brand/date."""
    config = schedule_for(session, brand_id)
    try:
        local = at.astimezone(ZoneInfo(str(config["timezone"])))
    except Exception:
        local = at
    if is_quiet_or_paused(config, at):
        return 0
    created = 0
    for report_type, hour, builder in (
        ("MORNING_BRIEFING", _integer(config["morning_briefing_hour"], 9), briefing),
        ("EVENING_REPORT", _integer(config["evening_report_hour"], 18), evening_report),
    ):
        if local.hour < hour:
            continue
        existing = session.scalar(
            select(OperationsReport).where(
                OperationsReport.brand_id == brand_id,
                OperationsReport.report_type == report_type,
                OperationsReport.local_date == local.date().isoformat(),
            )
        )
        if existing is None:
            session.add(
                OperationsReport(
                    brand_id=brand_id,
                    report_type=report_type,
                    local_date=local.date().isoformat(),
                    summary_json=_json_report(builder(session, brand_id, at)),
                )
            )
            created += 1
    if created:
        session.commit()
    return created


def pending_reports(session: Session, limit: int = 20) -> list[OperationsReport]:
    return list(
        session.scalars(
            select(OperationsReport)
            .where(OperationsReport.status == "PENDING_DELIVERY")
            .order_by(OperationsReport.created_at)
            .limit(limit)
        )
    )


def mark_report_delivered(
    session: Session, report_id: uuid.UUID, channel_id: int, message_id: int
) -> None:
    report = session.get(OperationsReport, report_id)
    if report is None or report.status != "PENDING_DELIVERY":
        return
    report.status = "DELIVERED"
    report.delivered_at = now()
    report.discord_channel_id = str(channel_id)
    report.discord_message_id = str(message_id)
    session.commit()


def timeline(session: Session, brand_id: uuid.UUID, limit: int = 50) -> list[dict[str, object]]:
    from app.audit.models import AuditEvent

    events = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.brand_id == brand_id)
        .order_by(AuditEvent.created_at.desc())
        .limit(limit)
    )
    return [
        {"at": item.created_at.isoformat(), "event": item.event_name.replace(".", " ").replace("_", " ").title(), "entity": item.entity_type.replace("_", " ").title()}
        for item in events
    ]


def _open_task(session: Session, brand_id: uuid.UUID, task_type: str, title: str, reason: str, action: str, priority: str = "NORMAL") -> None:
    key = task_type.lower()
    existing = session.scalar(select(OperatorTask).where(OperatorTask.brand_id == brand_id, OperatorTask.dedupe_key == key, OperatorTask.status == "OPEN"))
    if existing is None:
        session.add(OperatorTask(brand_id=brand_id, priority=priority, task_type=task_type, dedupe_key=key, title=title, reason=reason, action_label=action))
        return
    # The task remains one grouped work item, but must describe the current
    # pending work rather than an old scheduler observation.
    existing.priority = priority
    existing.title = title
    existing.reason = reason
    existing.action_label = action


def _complete_task(session: Session, brand_id: uuid.UUID, task_type: str) -> None:
    task = session.scalar(
        select(OperatorTask).where(
            OperatorTask.brand_id == brand_id,
            OperatorTask.dedupe_key == task_type.lower(),
            OperatorTask.status == "OPEN",
        )
    )
    if task is not None:
        task.status = "COMPLETED"
        task.completed_at = now()


def refresh_operational_state(session: Session, brand_id: uuid.UUID) -> dict[str, object]:
    """Create grouped tasks/alerts. Repeated scheduler ticks never create duplicates."""
    health = health_summary(session, brand_id)
    if health["review_items"]:
        _open_task(session, brand_id, "REVIEW_CONTENT", "Review content", f"{health['review_items']} item(s) need a creative decision.", "Review Content", "HIGH")
    else:
        _complete_task(session, brand_id, "REVIEW_CONTENT")
    if health["queue"]["failed"]:  # type: ignore[index]
        _open_task(session, brand_id, "RETRY_FAILED", "Review failed processing", "One or more projects failed processing.", "Review Failures", "HIGH")
    if not health["connected_destinations"]:
        _open_task(session, brand_id, "CONNECT_DESTINATION", "Connect a publishing destination", "No active destination is connected for this brand.", "Open Publishing")
    if health["state"] in {"Degraded", "Critical"}:
        alert = session.scalar(select(OperationsAlert).where(OperationsAlert.brand_id == brand_id, OperationsAlert.dedupe_key == "brand-health", OperationsAlert.status == "OPEN"))
        if alert is None:
            session.add(OperationsAlert(brand_id=brand_id, severity="CRITICAL" if health["state"] == "Critical" else "WARNING", category="BRAND_HEALTH", dedupe_key="brand-health", status="OPEN", summary=f"Brand health is {health['state']}.", first_seen_at=now(), last_seen_at=now()))
        else:
            alert.last_seen_at = now()
            alert.occurrences += 1
    session.commit()
    return health


def run_due_operations(session: Session, at: datetime | None = None) -> int:
    """A bounded scheduler tick. Quiet or paused brands are not notified or changed."""
    at = at or now()
    processed = 0
    for brand in session.scalars(select(Brand).where(Brand.is_active)):
        if is_quiet_or_paused(schedule_for(session, brand.id), at):
            continue
        refresh_operational_state(session, brand.id)
        create_due_reports(session, brand.id, at)
        processed += 1
    return processed
