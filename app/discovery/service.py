"""Persisted, review-first public discovery orchestration."""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.common.config import Settings, get_settings
from app.discovery.models import (
    DiscoveredMedia,
    DiscoveryRun,
    DiscoverySource,
    DiscoveryStatus,
    DuplicateStatus,
)
from app.discovery.providers import (
    DiscoveryProvider,
    ProviderError,
    ProviderMedia,
    default_providers,
)
from app.production.service import ProductionError, create_project
from app.production.source_quality import normalize_text


class DiscoveryError(ProductionError):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


def _weights(settings: Settings) -> tuple[dict[str, float], dict[str, float]]:
    try:
        payload = (
            yaml.safe_load(open(settings.discovery_relevance_weights_path, encoding="utf-8")) or {}
        )
    except OSError:
        payload = {}

    def as_weights(value: object) -> dict[str, float]:
        return (
            {str(key): float(item) for key, item in value.items() if isinstance(item, int | float)}
            if isinstance(value, dict)
            else {}
        )

    return as_weights(payload.get("weights") if isinstance(payload, dict) else None), as_weights(
        payload.get("penalties") if isinstance(payload, dict) else None
    )


def relevance(
    source: DiscoverySource, item: ProviderMedia, settings: Settings | None = None
) -> tuple[float, list[str], list[str], str]:
    settings = settings or get_settings()
    weights, penalties = _weights(settings)
    text = normalize_text(f"{item.title or ''} {item.description or ''}")
    config = source.configuration_json
    raw_keywords = config.get("keywords")
    raw_excluded = config.get("excluded_keywords")
    raw_categories = config.get("categories")
    keywords = (
        [normalize_text(str(value)) for value in raw_keywords]
        if isinstance(raw_keywords, list)
        else []
    )
    excluded = (
        [normalize_text(str(value)) for value in raw_excluded]
        if isinstance(raw_excluded, list)
        else []
    )
    categories = (
        [normalize_text(str(value)) for value in raw_categories]
        if isinstance(raw_categories, list)
        else []
    )
    matched: list[str] = []
    negative: list[str] = []
    score = 0.0

    def add(name: str, matched_value: bool, default: float) -> None:
        nonlocal score
        if matched_value:
            score += weights.get(name, default)
            matched.append(name)

    add("official_source", source.trusted, 30)
    add(
        "agency_match",
        bool(source.agency_reference and normalize_text(source.agency_reference) in text),
        20,
    )
    add("keyword_match", any(value and value in text for value in keywords), 15)
    add("category_match", any(value and value in text for value in categories), 15)
    add("usable_video", bool(item.canonical_url), 10)
    if item.published_at and item.published_at >= utcnow() - timedelta(
        days=settings.discovery_max_item_age_days
    ):
        add("recency", True, 10)
    if any(value and value in text for value in excluded):
        score -= penalties.get("excluded_keyword", 50)
        negative.append("excluded_keyword")
    if not item.canonical_url:
        score -= penalties.get("missing_media", 30)
        negative.append("missing_media")
    score = max(0.0, min(100.0, score))
    action = (
        DiscoveryStatus.NEEDS_REVIEW
        if score >= settings.discovery_min_relevance_score and not negative
        else DiscoveryStatus.LOW_RELEVANCE
    )
    return score, matched, negative, action


def duplicate_status(session: Session, source: DiscoverySource, item: ProviderMedia) -> str:
    if session.scalar(
        select(DiscoveredMedia.id).where(
            DiscoveredMedia.discovery_source_id == source.id,
            DiscoveredMedia.provider_item_id == item.item_id,
        )
    ):
        return DuplicateStatus.EXACT
    if session.scalar(
        select(DiscoveredMedia.id).where(DiscoveredMedia.canonical_url == item.canonical_url)
    ):
        return DuplicateStatus.EXACT
    title = normalize_text(item.title)
    if title and session.scalar(
        select(DiscoveredMedia.id).where(
            DiscoveredMedia.platform == source.platform, DiscoveredMedia.title.is_not(None)
        )
    ):
        existing = session.scalars(
            select(DiscoveredMedia).where(DiscoveredMedia.platform == source.platform).limit(200)
        )
        if any(normalize_text(record.title) == title for record in existing):
            return DuplicateStatus.PROBABLE
    return DuplicateStatus.NOT_DUPLICATE


def run_source(
    session: Session,
    actor_id: uuid.UUID,
    source: DiscoverySource,
    providers: dict[str, DiscoveryProvider] | None = None,
    settings: Settings | None = None,
) -> DiscoveryRun:
    settings = settings or get_settings()
    providers = providers or default_providers(settings)
    if not settings.discovery_enabled:
        raise DiscoveryError("DISCOVERY_DISABLED", "discovery is disabled by configuration")
    if not source.enabled:
        raise DiscoveryError("DISCOVERY_SOURCE_DISABLED", "discovery source is disabled")
    provider = providers.get(source.provider)
    if provider is None:
        raise DiscoveryError(
            "DISCOVERY_PROVIDER_UNKNOWN", "configured discovery provider is unavailable"
        )
    now = utcnow()
    run = DiscoveryRun(
        provider=source.provider, discovery_source_id=source.id, brand_id=source.brand_id, started_at=now, status="RUNNING"
    )
    session.add(run)
    source.last_attempted_poll_at = now
    session.commit()
    try:
        result = provider.poll(source.configuration_json, run.cursor)
        run.fetched_count = len(result.items)
        run.cursor = result.cursor
        for item in result.items[: settings.discovery_result_limit]:
            duplicate = duplicate_status(session, source, item)
            if duplicate == DuplicateStatus.EXACT:
                run.duplicate_count += 1
                continue
            score, matched, negative, lifecycle = relevance(source, item, settings)
            media = DiscoveredMedia(
                discovery_source_id=source.id,
                brand_id=source.brand_id,
                provider_item_id=item.item_id[:500],
                canonical_url=item.canonical_url,
                submitted_url=item.canonical_url,
                platform=source.platform,
                title=item.title,
                description=item.description,
                uploader=item.uploader,
                uploader_id=item.uploader_id,
                published_at=item.published_at,
                discovered_at=now,
                duration_seconds=item.duration_seconds,
                width=item.width,
                height=item.height,
                thumbnail_url=item.thumbnail_url,
                view_count=item.view_count,
                language=item.language,
                agency_hints=[source.agency_reference] if source.agency_reference else [],
                incident_hints=matched,
                discovery_score=score,
                quality_score=None,
                source_confidence=1.0 if source.trusted else 0.0,
                watermark_status="UNKNOWN",
                duplicate_status=duplicate,
                lifecycle_status=DiscoveryStatus.DUPLICATE
                if duplicate == DuplicateStatus.PROBABLE
                else lifecycle,
                metadata_json={
                    **item.metadata,
                    "matched_signals": matched,
                    "negative_signals": negative,
                    "discovery_reason": f"{source.name} via {source.provider}",
                },
            )
            session.add(media)
            session.flush()
            run.new_count += 1
            session.add(
                AuditEvent(
                    actor_id=actor_id,
                    entity_type="discovered_media",
                    entity_id=media.id,
                    brand_id=media.brand_id,
                    event_name="discovery.media.discovered",
                    payload={"provider": source.provider, "score": score, "duplicate": duplicate},
                )
            )
        run.status = "SUCCEEDED"
        run.finished_at = utcnow()
        run.metrics_json = {"rate_limit_remaining": result.rate_limit_remaining}
        source.last_successful_poll_at = now
        source.failure_count = 0
        source.last_error_category = None
        source.next_poll_at = now + timedelta(
            seconds=source.polling_interval_seconds
            + random.randint(0, min(60, source.polling_interval_seconds // 10))
        )
    except ProviderError as error:
        run.status = "FAILED"
        run.finished_at = utcnow()
        run.failed_count = 1
        run.error_summary = str(error)[:2000]
        source.failure_count += 1
        source.last_error_category = error.category
        source.next_poll_at = now + timedelta(
            seconds=settings.discovery_retry_backoff_seconds
            * (2 ** min(source.failure_count, settings.discovery_retry_count))
        )
    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="discovery_run",
            entity_id=run.id,
            brand_id=run.brand_id,
            event_name="discovery.run.completed",
            payload={
                "status": run.status,
                "new_count": run.new_count,
                "duplicate_count": run.duplicate_count,
            },
        )
    )
    session.commit()
    return run


def approve_media(
    session: Session, actor_id: uuid.UUID, media: DiscoveredMedia, expected_version: int
) -> DiscoveredMedia:
    if expected_version != media.review_version:
        raise DiscoveryError("STALE_DISCOVERY_ACTION", "discovery item changed; refresh the queue")
    if media.lifecycle_status == DiscoveryStatus.PROJECT_CREATED:
        return media
    if media.lifecycle_status in {DiscoveryStatus.REJECTED, DiscoveryStatus.ARCHIVED}:
        raise DiscoveryError("DISCOVERY_NOT_APPROVABLE", "discovery item cannot be approved")
    project = create_project(
        session, actor_id, media.submitted_url, title=media.title, channel=media.uploader, brand_id=media.brand_id
    )
    media.production_project_id = project.id
    media.lifecycle_status = DiscoveryStatus.PROJECT_CREATED
    media.review_version += 1
    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="discovered_media",
            entity_id=media.id,
            brand_id=media.brand_id,
            event_name="discovery.media.approved",
            payload={"production_project_id": str(project.id)},
        )
    )
    session.commit()
    return media


def reject_media(
    session: Session,
    actor_id: uuid.UUID,
    media: DiscoveredMedia,
    expected_version: int,
    reason: str | None = None,
) -> DiscoveredMedia:
    if expected_version != media.review_version:
        raise DiscoveryError("STALE_DISCOVERY_ACTION", "discovery item changed; refresh the queue")
    media.lifecycle_status = DiscoveryStatus.REJECTED
    media.review_version += 1
    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="discovered_media",
            entity_id=media.id,
            event_name="discovery.media.rejected",
            reason=reason,
        )
    )
    session.commit()
    return media
