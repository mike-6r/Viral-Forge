"""Manual, bounded RSS/Atom ingestion; scheduling is intentionally deferred."""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from xml.etree.ElementTree import Element

from defusedxml import ElementTree
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.common.config import Settings, get_settings
from app.common.errors import DomainError, PreconditionError
from app.content.lifecycle import transition
from app.content.models import ContentItem, ContentSource, ContentStatus
from app.ingestion.http import SafeFetchError, SafeOutboundHttpClient
from app.ingestion.models import (
    FeedEntry,
    FeedSubscription,
    IngestionJob,
    IngestionMethod,
    IngestionStatus,
)
from app.ingestion.policy import enforce_url_policy
from app.ingestion.url import normalize_url
from app.sources.models import Source, SourcePolicy, SourceStatus

FEED_TYPES = {"application/rss+xml", "application/atom+xml", "application/xml", "text/xml"}
ACTIVE_STATUSES = {"ACTIVE", "FAILING"}


def get_feed_client() -> SafeOutboundHttpClient:
    return SafeOutboundHttpClient()


class FeedError(DomainError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class FeedLimits:
    recent_window_days: int
    maximum_items: int


def _local(node: Element, name: str) -> Element | None:
    return next((child for child in node if child.tag.rsplit("}", 1)[-1] == name), None)


def _text(node: Element | None, limit: int = 500) -> str | None:
    return " ".join(node.text.split())[:limit] if node is not None and node.text else None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def ensure_utc(value: datetime) -> datetime:
    """Normalize database values; SQLite does not preserve timezone offsets."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _entry_order_key(entry: dict[str, object | None]) -> tuple[float, int, str]:
    event_time = entry["published_at"] or entry["updated_at"]
    timestamp = -event_time.timestamp() if isinstance(event_time, datetime) else float("inf")
    position = entry["position"]
    return (
        timestamp,
        position if isinstance(position, int) else 0,
        str(entry["id"] or entry["link"] or ""),
    )


def parse_feed(
    body: bytes,
) -> tuple[IngestionMethod, dict[str, str | None], list[dict[str, object | None]]]:
    try:
        root = ElementTree.fromstring(body)
    except Exception as error:
        raise FeedError("FEED_PARSE_FAILURE", "feed XML could not be parsed") from error
    kind = root.tag.rsplit("}", 1)[-1]
    if kind == "rss":
        channel = _local(root, "channel")
        if channel is None:
            raise FeedError("UNSUPPORTED_FEED_FORMAT", "RSS feed has no channel")
        entries: list[dict[str, object | None]] = []
        for position, item in enumerate(
            child for child in channel if child.tag.rsplit("}", 1)[-1] == "item"
        ):
            entries.append(
                {
                    "id": _text(_local(item, "guid"), 1024),
                    "title": _text(_local(item, "title")),
                    "link": _text(_local(item, "link"), 2048),
                    "summary": _text(_local(item, "description"), 2000),
                    "author": _text(_local(item, "author")),
                    "published_at": _parse_date(_text(_local(item, "pubDate"), 100)),
                    "updated_at": None,
                    "position": position,
                }
            )
        return (
            IngestionMethod.RSS_FEED,
            {
                "title": _text(_local(channel, "title")),
                "description": _text(_local(channel, "description"), 2000),
                "link": _text(_local(channel, "link"), 2048),
                "language": _text(_local(channel, "language"), 50),
            },
            entries,
        )
    if kind == "feed":
        entries = []
        for position, item in enumerate(
            child for child in root if child.tag.rsplit("}", 1)[-1] == "entry"
        ):
            link = _local(item, "link")
            entries.append(
                {
                    "id": _text(_local(item, "id"), 1024),
                    "title": _text(_local(item, "title")),
                    "link": link.get("href") if link is not None else None,
                    "summary": _text(_local(item, "summary"), 2000)
                    or _text(_local(item, "content"), 2000),
                    "author": _text(_local(_local(item, "author") or item, "name")),
                    "published_at": _parse_date(_text(_local(item, "published"), 100)),
                    "updated_at": _parse_date(_text(_local(item, "updated"), 100)),
                    "position": position,
                }
            )
        return (
            IngestionMethod.ATOM_FEED,
            {
                "title": _text(_local(root, "title")),
                "description": _text(_local(root, "subtitle"), 2000),
                "link": None,
                "language": None,
            },
            entries,
        )
    raise FeedError("UNSUPPORTED_FEED_FORMAT", "only RSS and Atom feeds are supported")


def _policy(session: Session, source: Source) -> SourcePolicy | None:
    return session.scalar(
        select(SourcePolicy)
        .where(SourcePolicy.source_id == source.id)
        .order_by(SourcePolicy.created_at.desc())
    )


def effective_limits(
    feed: FeedSubscription,
    policy: SourcePolicy | None,
    settings: Settings | None = None,
    recent_window_override: int | None = None,
    max_items_override: int | None = None,
) -> FeedLimits:
    settings = settings or get_settings()
    requested_window = (
        recent_window_override
        if recent_window_override is not None
        else feed.recent_item_window_days
        or (
            policy.feed_recent_window_days
            if policy and policy.feed_recent_window_days
            else settings.feed_default_recent_window_days
        )
    )
    requested_maximum = (
        max_items_override
        if max_items_override is not None
        else feed.max_items_per_run
        or (policy.max_feed_items_per_run if policy else settings.feed_default_max_items_per_run)
    )
    return FeedLimits(
        max(
            settings.feed_min_recent_window_days,
            min(
                requested_window,
                settings.feed_max_recent_window_days,
                settings.feed_absolute_max_historical_age_days,
            ),
        ),
        max(1, min(requested_maximum, settings.feed_absolute_max_items_per_run)),
    )


def next_eligible_run(
    feed: FeedSubscription, policy: SourcePolicy | None, now: datetime | None = None
) -> datetime | None:
    if feed.last_checked_at is None:
        return None
    settings = get_settings()
    interval = max(
        feed.polling_interval_seconds,
        policy.min_feed_run_interval_seconds
        if policy and policy.min_feed_run_interval_seconds
        else 0,
        settings.feed_min_polling_interval_seconds,
    )
    return ensure_utc(feed.last_checked_at) + timedelta(seconds=interval)


async def validate_feed(
    session: Session,
    actor_id: uuid.UUID,
    feed: FeedSubscription,
    client: SafeOutboundHttpClient,
    correlation_id: str | None = None,
) -> FeedSubscription:
    source = session.get(Source, feed.source_id)
    if source is None or source.status is not SourceStatus.ACTIVE:
        raise FeedError("SOURCE_INACTIVE", "feed source must be active")
    try:
        enforce_url_policy(source, _policy(session, source), feed.feed_url, True, feed.feed_type)
        result = await client.fetch(
            feed.feed_url, correlation_id, accepted_content_types=FEED_TYPES
        )
        feed_type, metadata, _ = parse_feed(result.body)
        feed.feed_type = feed_type
        feed.status = "ACTIVE"
        feed.final_url = result.final_url
        feed.title = metadata["title"]
        feed.description = metadata["description"]
        feed.site_url = metadata["link"]
        feed.language = metadata["language"]
        feed.etag = result.headers.get("etag", "")[:255] or None
        feed.last_modified = result.headers.get("last-modified", "")[:255] or None
        feed.last_error_category = None
        feed.last_error_message = None
        session.add(
            AuditEvent(
                actor_id=actor_id,
                entity_type="feed",
                entity_id=feed.id,
                event_name="feed.validation.succeeded",
                correlation_id=correlation_id,
            )
        )
        session.commit()
    except (SafeFetchError, FeedError, PreconditionError) as error:
        feed.status = "FAILING"
        feed.last_error_category = getattr(error, "category", "POLICY_VIOLATION")
        feed.last_error_message = str(error)[:2_000]
        session.add(
            AuditEvent(
                actor_id=actor_id,
                entity_type="feed",
                entity_id=feed.id,
                event_name="feed.validation.failed",
                correlation_id=correlation_id,
                payload={"category": feed.last_error_category},
            )
        )
        session.commit()
    return feed


async def register_feed(
    session: Session,
    actor_id: uuid.UUID,
    source_id: uuid.UUID,
    feed_url: str,
    client: SafeOutboundHttpClient,
    correlation_id: str | None = None,
    polling_interval_seconds: int | None = None,
    recent_item_window_days: int | None = None,
    max_items_per_run: int | None = None,
    notes: str | None = None,
    idempotency_key: str | None = None,
) -> FeedSubscription:
    source = session.get(Source, source_id)
    if source is None or source.status is not SourceStatus.ACTIVE:
        raise FeedError("SOURCE_INACTIVE", "feed source must be active")
    normalized = normalize_url(feed_url)
    if session.scalar(select(FeedSubscription).where(FeedSubscription.feed_url == normalized)):
        raise FeedError("IDEMPOTENCY_CONFLICT", "feed URL is already registered")
    settings = get_settings()
    policy = _policy(session, source)
    feed = FeedSubscription(
        source_id=source.id,
        feed_url=normalized,
        feed_type=IngestionMethod.RSS_FEED,
        status="PENDING_VALIDATION",
        polling_interval_seconds=max(
            polling_interval_seconds or 3600,
            policy.min_feed_run_interval_seconds
            if policy and policy.min_feed_run_interval_seconds
            else 0,
            settings.feed_min_polling_interval_seconds,
        ),
        recent_item_window_days=recent_item_window_days
        or (
            policy.feed_recent_window_days
            if policy and policy.feed_recent_window_days
            else settings.feed_default_recent_window_days
        ),
        max_items_per_run=max_items_per_run
        or (policy.max_feed_items_per_run if policy else settings.feed_default_max_items_per_run),
        notes=notes,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
    )
    limits = effective_limits(feed, policy, settings)
    feed.recent_item_window_days = limits.recent_window_days
    feed.max_items_per_run = limits.maximum_items
    session.add(feed)
    session.flush()
    return await validate_feed(session, actor_id, feed, client, correlation_id)


def change_feed_status(
    session: Session,
    actor_id: uuid.UUID,
    feed: FeedSubscription,
    target: str,
    correlation_id: str | None,
    reason: str | None = None,
) -> FeedSubscription:
    source = session.get(Source, feed.source_id)
    if target == "activate":
        if feed.status in {"BLOCKED", "REJECTED", "ARCHIVED"}:
            raise FeedError("FEED_STATUS_INVALID", "feed requires revalidation before activation")
        if source is None or source.status is not SourceStatus.ACTIVE:
            raise FeedError("SOURCE_INACTIVE", "feed source must be active")
        enforce_url_policy(source, _policy(session, source), feed.feed_url, True, feed.feed_type)
        feed.status = "ACTIVE"
    elif target == "pause":
        feed.status = "PAUSED"
    elif target == "block":
        if not reason:
            raise FeedError("BLOCK_REASON_REQUIRED", "a block reason is required")
        feed.status = "BLOCKED"
        feed.active_lease_until = None
        feed.active_job_id = None
    else:
        raise FeedError("FEED_STATUS_INVALID", "unsupported feed status transition")
    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="feed",
            entity_id=feed.id,
            event_name=f"feed.{target}",
            reason=reason,
            correlation_id=correlation_id,
        )
    )
    session.commit()
    return feed


async def run_feed(
    session: Session,
    actor_id: uuid.UUID,
    feed_id: uuid.UUID,
    client: SafeOutboundHttpClient,
    correlation_id: str | None = None,
    recent_window_override: int | None = None,
    max_items_override: int | None = None,
) -> IngestionJob:
    feed = session.get(FeedSubscription, feed_id)
    if feed is None or feed.status != "ACTIVE":
        raise FeedError("FEED_INACTIVE", "feed is not active")
    source = session.get(Source, feed.source_id)
    if source is None or source.status is not SourceStatus.ACTIVE:
        raise FeedError("SOURCE_INACTIVE", "feed source must be active")
    policy = _policy(session, source)
    try:
        enforce_url_policy(source, policy, feed.feed_url, True, feed.feed_type)
    except PreconditionError as error:
        raise FeedError("POLICY_VIOLATION", "source policy rejected the feed") from error
    now = datetime.now(UTC)
    eligible = next_eligible_run(feed, policy, now)
    if eligible is not None and now < eligible:
        raise FeedError("FEED_RUN_TOO_SOON", f"feed is next eligible at {eligible.isoformat()}")
    if feed.active_lease_until and ensure_utc(feed.active_lease_until) > now:
        raise FeedError("FEED_ALREADY_RUNNING", "feed has an active run")
    limits = effective_limits(
        feed,
        policy,
        recent_window_override=recent_window_override,
        max_items_override=max_items_override,
    )
    job = IngestionJob(
        method=feed.feed_type,
        status=IngestionStatus.RUNNING,
        actor_id=actor_id,
        source_id=source.id,
        requested_url=feed.feed_url,
        idempotency_key=str(uuid.uuid4()),
        attempts=1,
        started_at=now,
        heartbeat_at=now,
        correlation_id=correlation_id,
        result_metadata={
            "recent_window_days": limits.recent_window_days,
            "maximum_items": limits.maximum_items,
        },
    )
    session.add(job)
    session.flush()
    job_id = job.id
    feed.active_job_id = job.id
    feed.active_lease_until = now + timedelta(minutes=5)
    session.commit()
    stats: dict[str, int | bool] = {
        "entries_seen": 0,
        "entries_eligible": 0,
        "entries_imported": 0,
        "entries_skipped_old": 0,
        "entries_skipped_limit": 0,
        "duplicate_entries": 0,
        "malformed_entries": 0,
        "not_modified": False,
    }
    try:
        headers = {
            key: value
            for key, value in {
                "If-None-Match": feed.etag,
                "If-Modified-Since": feed.last_modified,
            }.items()
            if value
        }
        result = await client.fetch(
            feed.feed_url, correlation_id, accepted_content_types=FEED_TYPES, extra_headers=headers
        )
        feed = session.get(FeedSubscription, feed_id)
        persisted_job = session.get(IngestionJob, job_id)
        assert feed is not None and persisted_job is not None
        job = persisted_job
        if result.status_code == 304:
            stats["not_modified"] = True
        else:
            feed_type, _, entries = parse_feed(result.body)
            if feed_type is not feed.feed_type:
                raise FeedError("UNSUPPORTED_FEED_FORMAT", "feed type changed")
            stats["entries_seen"] = len(entries)
            cutoff = now - timedelta(days=limits.recent_window_days)
            future_limit = now + timedelta(seconds=get_settings().feed_future_date_skew_seconds)
            eligible_entries: list[dict[str, object | None]] = []
            for entry in entries[: get_settings().feed_max_parsed_entries]:
                event_time = entry["published_at"] or entry["updated_at"]
                if isinstance(event_time, datetime) and (
                    event_time < cutoff or event_time > future_limit
                ):
                    stats["entries_skipped_old"] += 1
                    continue
                eligible_entries.append(entry)
            eligible_entries.sort(key=_entry_order_key)
            stats["entries_eligible"] = len(eligible_entries)
            for position, entry in enumerate(eligible_entries):
                if position >= limits.maximum_items:
                    stats["entries_skipped_limit"] += 1
                    continue
                identity = str(
                    entry["id"]
                    or entry["link"]
                    or hashlib.sha256(repr(sorted(entry.items())).encode()).hexdigest()
                )[:1024]
                if session.scalar(
                    select(FeedEntry.id).where(
                        FeedEntry.subscription_id == feed.id, FeedEntry.entry_guid == identity
                    )
                ):
                    stats["duplicate_entries"] += 1
                    continue
                try:
                    link = normalize_url(str(entry["link"])) if entry["link"] else None
                except DomainError:
                    stats["malformed_entries"] += 1
                    session.add(
                        FeedEntry(
                            subscription_id=feed.id,
                            entry_guid=identity,
                            title=str(entry["title"] or "")[:500] or None,
                            identity_strategy="GUID" if entry["id"] else "URL",
                            import_outcome="SKIPPED_MALFORMED",
                            failure_category="INVALID_URL",
                        )
                    )
                    continue
                item = ContentItem(
                    title=str(entry["title"] or "Untitled feed entry")[:500],
                    description=str(entry["summary"] or "")[:2000] or None,
                    status=ContentStatus.DISCOVERED,
                    source_provenance_complete=True,
                )
                session.add(item)
                session.flush()
                session.add_all(
                    [
                        FeedEntry(
                            subscription_id=feed.id,
                            entry_guid=identity,
                            content_id=item.id,
                            link=link,
                            title=str(entry["title"] or "")[:500] or None,
                            author=str(entry["author"] or "")[:500] or None,
                            published_at=entry["published_at"]
                            if isinstance(entry["published_at"], datetime)
                            else None,
                            updated_at_source=entry["updated_at"]
                            if isinstance(entry["updated_at"], datetime)
                            else None,
                            identity_strategy="GUID" if entry["id"] else "URL",
                            import_outcome="IMPORTED",
                            raw_metadata={"position": entry["position"]},
                        ),
                        ContentSource(
                            content_id=item.id,
                            source_id=source.id,
                            source_url=link or f"feed://{feed.id}/{identity}",
                        ),
                    ]
                )
                transition(
                    session,
                    item,
                    ContentStatus.IMPORTED,
                    actor_id,
                    "feed entry imported",
                    correlation_id,
                )
                transition(
                    session,
                    item,
                    ContentStatus.SOURCE_VERIFICATION_REQUIRED,
                    actor_id,
                    "feed source requires verification",
                    correlation_id,
                )
                stats["entries_imported"] += 1
            feed.etag = result.headers.get("etag", "")[:255] or feed.etag
            feed.last_modified = result.headers.get("last-modified", "")[:255] or feed.last_modified
        job.status = IngestionStatus.SUCCEEDED
        job.completed_at = now
        job.progress = 100
        job.result_metadata = {
            **(job.result_metadata or {}),
            **stats,
            "next_eligible_run": (
                now
                + timedelta(
                    seconds=max(
                        feed.polling_interval_seconds,
                        get_settings().feed_min_polling_interval_seconds,
                    )
                )
            ).isoformat(),
        }
        feed.last_checked_at = now
        feed.last_success_at = now
        feed.consecutive_failures = 0
        feed.active_lease_until = None
        feed.active_job_id = None
        session.commit()
        return job
    except (SafeFetchError, FeedError) as error:
        session.rollback()
        persisted_job = session.get(IngestionJob, job_id)
        feed = session.get(FeedSubscription, feed_id)
        assert persisted_job is not None and feed is not None
        job = persisted_job
        job.status = IngestionStatus.FAILED
        job.error_category = getattr(error, "category", "FEED_PARSE_FAILURE")
        job.error_message = str(error)[:2_000]
        job.completed_at = datetime.now(UTC)
        job.result_metadata = {**(job.result_metadata or {}), **stats}
        feed.status = "FAILING"
        feed.consecutive_failures += 1
        feed.active_lease_until = None
        feed.active_job_id = None
        session.commit()
        return job
