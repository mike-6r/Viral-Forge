"""Durable operator-recorded publishing and sparse manual analytics. No provider calls."""

import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.analytics.models import PostAnalyticsSnapshot
from app.audit.models import AuditEvent
from app.content.models import MediaAsset
from app.content_packages.models import ContentPackage, ContentPackageStatus
from app.manual_publishing.models import ManualAnalyticsCheckpoint, ManualPublication
from app.production.models import ProductionClip

PLATFORM_HOSTS = {
    "TIKTOK": ("tiktok.com",), "YOUTUBE": ("youtube.com", "youtu.be"),
    "INSTAGRAM": ("instagram.com",), "FACEBOOK": ("facebook.com",), "OTHER": (),
}
CHECKPOINTS = (("1H", 1), ("6H", 6), ("24H", 24), ("72H", 72), ("7D", 168))


class ManualPublicationError(Exception):
    pass


def validate_public_url(platform: str, value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ManualPublicationError("a public HTTPS post URL is required")
    normalized = platform.upper()
    if normalized not in PLATFORM_HOSTS:
        raise ManualPublicationError("unsupported manual publication platform")
    hosts = PLATFORM_HOSTS[normalized]
    host = parsed.hostname or ""
    if hosts and not any(host == item or host.endswith(f".{item}") for item in hosts):
        raise ManualPublicationError("the post URL does not match the selected platform")
    return value


def record_manual_publication(session: Session, actor_id: uuid.UUID, clip: ProductionClip, package: ContentPackage, asset: MediaAsset, *, platform: str, destination_label: str, public_post_url: str, published_at: datetime, notes: str | None = None) -> ManualPublication:
    public_post_url = validate_public_url(platform, public_post_url)
    if package.clip_id != clip.id or package.status != ContentPackageStatus.APPROVED:
        raise ManualPublicationError("an approved content package for this exact clip is required")
    if asset.clip_id != clip.id or asset.asset_type != "RENDERED_CLIP" or asset.brand_id != clip.brand_id:
        raise ManualPublicationError("the authoritative rendered media asset is required")
    existing = session.scalar(select(ManualPublication).where(ManualPublication.platform == platform.upper(), ManualPublication.public_post_url == public_post_url))
    if existing is not None:
        raise ManualPublicationError("that platform post URL is already recorded")
    publication = ManualPublication(brand_id=clip.brand_id, project_id=clip.project_id, clip_id=clip.id, media_asset_id=asset.id, content_package_id=package.id, content_package_version=package.review_version, platform=platform.upper(), destination_label=destination_label.strip(), public_post_url=public_post_url, published_at=published_at.astimezone(UTC), recorded_by_id=actor_id, notes=notes, analytics_eligibility="MANUAL_ONLY")
    session.add(publication)
    session.flush()
    for key, hours in CHECKPOINTS:
        session.add(ManualAnalyticsCheckpoint(manual_publication_id=publication.id, brand_id=clip.brand_id, checkpoint_key=key, due_at=publication.published_at + timedelta(hours=hours)))
    session.add(AuditEvent(actor_id=actor_id, entity_type="manual_publication", entity_id=publication.id, brand_id=clip.brand_id, event_name="manual_publication.recorded", payload={"platform": publication.platform, "clip_id": str(clip.id)}))
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise ManualPublicationError("that platform post URL is already recorded") from error
    return publication


def add_manual_metrics(session: Session, actor_id: uuid.UUID, publication: ManualPublication, metrics: dict[str, object], captured_at: datetime, notes: str | None = None) -> PostAnalyticsSnapshot:
    snapshot = PostAnalyticsSnapshot(manual_publication_id=publication.id, clip_id=publication.clip_id, brand_id=publication.brand_id, provider=publication.platform, captured_at=captured_at.astimezone(UTC), collection_source="MANUAL", raw_metadata={"profile_visits": metrics.get("profile_visits"), "completion_rate": metrics.get("completion_rate"), "notes": notes} | {key: value for key, value in metrics.items() if key not in {"profile_visits", "completion_rate"}}, **{key: metrics.get(key) for key in ("views", "watch_time_seconds", "average_view_duration_seconds", "retention_percentage", "likes", "comments", "shares", "saves", "followers_gained", "clicks", "platform_revenue", "currency")})
    session.add(snapshot)
    for checkpoint in session.scalars(select(ManualAnalyticsCheckpoint).where(ManualAnalyticsCheckpoint.manual_publication_id == publication.id, ManualAnalyticsCheckpoint.status == "DUE", ManualAnalyticsCheckpoint.due_at <= captured_at)):
        checkpoint.status, checkpoint.completed_at = "COMPLETED", datetime.now(UTC)
    session.add(AuditEvent(actor_id=actor_id, entity_type="manual_publication", entity_id=publication.id, brand_id=publication.brand_id, event_name="manual_analytics.recorded", payload={"captured_at": captured_at.isoformat()}))
    session.commit()
    return snapshot


def update_checkpoint(
    session: Session,
    actor_id: uuid.UUID,
    checkpoint: ManualAnalyticsCheckpoint,
    action: str,
    notes: str | None = None,
    snooze_hours: int | None = None,
) -> ManualAnalyticsCheckpoint:
    """Record an explicit operator acknowledgement; reminders never create metrics."""
    normalized = action.upper()
    if normalized == "COMPLETE":
        checkpoint.status = "COMPLETED"
        checkpoint.completed_at = datetime.now(UTC)
    elif normalized == "SKIP":
        checkpoint.status = "SKIPPED"
        checkpoint.completed_at = datetime.now(UTC)
    elif normalized == "SNOOZE":
        if snooze_hours is None or not 1 <= snooze_hours <= 168:
            raise ManualPublicationError("snooze_hours must be between 1 and 168")
        checkpoint.status = "DUE"
        checkpoint.snoozed_until = datetime.now(UTC) + timedelta(hours=snooze_hours)
    else:
        raise ManualPublicationError("checkpoint action must be COMPLETE, SKIP, or SNOOZE")
    checkpoint.operator_notes = notes
    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="manual_analytics_checkpoint",
            entity_id=checkpoint.id,
            brand_id=checkpoint.brand_id,
            event_name="manual_analytics.checkpoint_updated",
            payload={"action": normalized},
        )
    )
    session.commit()
    return checkpoint
