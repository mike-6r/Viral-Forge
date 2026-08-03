"""Official-provider analytics collection and recommendation-only aggregation."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, TypedDict

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.models import AnalyticsRefreshRun, OperatorFeedbackLabel, PostAnalyticsSnapshot
from app.audit.models import AuditEvent
from app.brands.models import DestinationAccount
from app.common.config import Settings, get_settings
from app.content_packages.models import ContentPackage
from app.manual_publishing.models import ManualPublication
from app.production.models import ProductionClip, ProductionProject
from app.publishing.models import PublishRequest, PublishRequestStatus
from app.publishing.service import EnvironmentCredentialResolver, PublishingError


class AnalyticsError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code, self.message = code, message
        super().__init__(message)


class AnalyticsDashboard(TypedDict):
    published_posts: int
    views: int
    average_retention_percentage: float | None
    groups: dict[str, dict[str, dict[str, float | int]]]
    recommendations: list[str]


@dataclass(frozen=True)
class NormalizedMetrics:
    views: int | None = None
    watch_time_seconds: float | None = None
    average_view_duration_seconds: float | None = None
    retention_percentage: float | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    saves: int | None = None
    followers_gained: int | None = None
    clicks: int | None = None
    platform_revenue: float | None = None
    currency: str | None = None
    raw_metadata: dict[str, object] | None = None


class AnalyticsProvider(Protocol):
    provider_name: str
    def fetch(self, request: PublishRequest, account: DestinationAccount, clip: ProductionClip) -> NormalizedMetrics: ...


class YouTubeAnalyticsProvider:
    provider_name = "YOUTUBE"
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.resolver = EnvironmentCredentialResolver()

    def fetch(self, request: PublishRequest, account: DestinationAccount, clip: ProductionClip) -> NormalizedMetrics:
        if not self.settings.analytics_enabled or not self.settings.analytics_youtube_enabled:
            raise AnalyticsError("ANALYTICS_DISABLED", "YouTube analytics refresh is disabled by configuration")
        if not request.remote_post_id:
            raise AnalyticsError("REMOTE_POST_REQUIRED", "a successfully published YouTube post is required")
        try:
            token = self.resolver.resolve(account.credential_reference_id)
            response = httpx.get("https://youtubeanalytics.googleapis.com/v2/reports", params={"ids": "channel==MINE", "startDate": (datetime.now(UTC) - timedelta(days=365)).date().isoformat(), "endDate": datetime.now(UTC).date().isoformat(), "metrics": "views,estimatedMinutesWatched,averageViewDuration,likes,comments,shares,subscribersGained", "filters": f"video=={request.remote_post_id}"}, headers={"Authorization": f"Bearer {token}"}, timeout=self.settings.analytics_http_timeout_seconds)
        except (PublishingError, httpx.HTTPError) as error:
            raise AnalyticsError("ANALYTICS_CONNECTION_FAILED", "could not reach the official YouTube Analytics API") from error
        if response.status_code in {401, 403}:
            raise AnalyticsError("ANALYTICS_AUTH_FAILED", "YouTube rejected the analytics OAuth credential or scope")
        if response.status_code >= 400:
            raise AnalyticsError("ANALYTICS_PROVIDER_FAILED", "YouTube Analytics did not accept the metrics query")
        body = response.json()
        headers = [str(header.get("name")) for header in body.get("columnHeaders", [])]
        rows = body.get("rows", [])
        values = dict(zip(headers, rows[0], strict=False)) if rows else {}
        average = float(values["averageViewDuration"]) if values.get("averageViewDuration") is not None else None
        retention = (average / clip.duration_seconds * 100) if average is not None and clip.duration_seconds > 0 else None
        return NormalizedMetrics(views=int(values["views"]) if values.get("views") is not None else None, watch_time_seconds=float(values["estimatedMinutesWatched"]) * 60 if values.get("estimatedMinutesWatched") is not None else None, average_view_duration_seconds=average, retention_percentage=retention, likes=int(values["likes"]) if values.get("likes") is not None else None, comments=int(values["comments"]) if values.get("comments") is not None else None, shares=int(values["shares"]) if values.get("shares") is not None else None, followers_gained=int(values["subscribersGained"]) if values.get("subscribersGained") is not None else None, raw_metadata={"reported_columns": headers})


def _provider(name: str, settings: Settings | None = None) -> AnalyticsProvider:
    if name.upper() == "YOUTUBE":
        return YouTubeAnalyticsProvider(settings)
    raise AnalyticsError("UNSUPPORTED_ANALYTICS_PROVIDER", "only YouTube analytics is supported")


def persist_snapshot(session: Session, request: PublishRequest, metrics: NormalizedMetrics, source: str, captured_at: datetime | None = None) -> PostAnalyticsSnapshot:
    if metrics.platform_revenue is not None and source not in {"OFFICIAL_API", "OPERATOR_IMPORT"}:
        raise AnalyticsError("REVENUE_SOURCE_REQUIRED", "revenue requires an official API or explicit operator import")
    snapshot = PostAnalyticsSnapshot(publish_request_id=request.id, clip_id=request.clip_id, brand_id=request.brand_id, provider="YOUTUBE", captured_at=captured_at or datetime.now(UTC), collection_source=source, views=metrics.views, watch_time_seconds=metrics.watch_time_seconds, average_view_duration_seconds=metrics.average_view_duration_seconds, retention_percentage=metrics.retention_percentage, likes=metrics.likes, comments=metrics.comments, shares=metrics.shares, saves=metrics.saves, followers_gained=metrics.followers_gained, clicks=metrics.clicks, platform_revenue=metrics.platform_revenue, currency=metrics.currency, raw_metadata=metrics.raw_metadata or {})
    session.add(snapshot)
    session.commit()
    return snapshot


def refresh_brand(session: Session, actor_id: uuid.UUID | None, brand_id: uuid.UUID | None = None, settings: Settings | None = None) -> AnalyticsRefreshRun:
    settings = settings or get_settings()
    run = AnalyticsRefreshRun(brand_id=brand_id, provider="YOUTUBE", status="RUNNING")
    session.add(run)
    session.flush()
    statement = select(PublishRequest).where(PublishRequest.status == PublishRequestStatus.SUCCEEDED)
    if brand_id is not None:
        statement = statement.where(PublishRequest.brand_id == brand_id)
    requests = list(session.scalars(statement.limit(settings.analytics_refresh_batch_size)))
    for request in requests:
        account, clip = session.get(DestinationAccount, request.destination_account_id), session.get(ProductionClip, request.clip_id)
        run.processed_count += 1
        if account is None or clip is None:
            continue
        try:
            metrics = _provider(account.provider, settings).fetch(request, account, clip)
        except AnalyticsError as error:
            run.error_summary = error.message
            continue
        persist_snapshot(session, request, metrics, "OFFICIAL_API")
        run.snapshot_count += 1
    run.status = "COMPLETED" if not run.error_summary else "COMPLETED_WITH_ERRORS"
    session.add(AuditEvent(actor_id=actor_id, entity_type="analytics_refresh_run", entity_id=run.id, brand_id=brand_id, event_name="analytics.refresh.completed", payload={"processed": run.processed_count, "snapshots": run.snapshot_count}))
    session.commit()
    return run


def add_feedback(session: Session, actor_id: uuid.UUID, request: PublishRequest, label: str, value: str, notes: str | None) -> OperatorFeedbackLabel:
    feedback = OperatorFeedbackLabel(brand_id=request.brand_id, publish_request_id=request.id, clip_id=request.clip_id, actor_id=actor_id, label=label.strip().upper(), value=value.strip(), notes=notes)
    session.add(feedback)
    session.add(AuditEvent(actor_id=actor_id, entity_type="publish_request", entity_id=request.id, brand_id=request.brand_id, event_name="analytics.feedback.added", payload={"label": feedback.label, "value": feedback.value}))
    session.commit()
    return feedback


def dashboard(session: Session, brand_id: uuid.UUID) -> AnalyticsDashboard:
    snapshots = list(session.scalars(select(PostAnalyticsSnapshot).where(PostAnalyticsSnapshot.brand_id == brand_id).order_by(PostAnalyticsSnapshot.captured_at.desc())))
    latest: dict[uuid.UUID, PostAnalyticsSnapshot] = {}
    for snapshot in snapshots:
        parent_id = snapshot.publish_request_id or snapshot.manual_publication_id
        if parent_id is not None:
            latest.setdefault(parent_id, snapshot)
    rows = list(latest.values())
    total_views = sum(item.views or 0 for item in rows)
    average_retention = sum(item.retention_percentage or 0 for item in rows) / len([item for item in rows if item.retention_percentage is not None]) if any(item.retention_percentage is not None for item in rows) else None
    groups: dict[str, dict[str, dict[str, float | int]]] = {key: {} for key in ("source", "topic", "duration", "hook", "posting_time")}
    for item in rows:
        request = (
            session.get(PublishRequest, item.publish_request_id)
            if item.publish_request_id
            else None
        )
        manual = (
            session.get(ManualPublication, item.manual_publication_id)
            if item.manual_publication_id
            else None
        )
        clip = session.get(ProductionClip, item.clip_id)
        if clip is None or (request is None and manual is None):
            continue
        project = session.get(ProductionProject, clip.project_id)
        if request is not None:
            package_id = request.content_package_id
            published_at = request.confirmed_at or request.created_at
        else:
            assert manual is not None
            package_id = manual.content_package_id
            published_at = manual.published_at
        package = session.get(ContentPackage, package_id)
        published_hour = (
            published_at.isoformat()[11:13] + ":00"
            if isinstance(published_at, datetime)
            else "Unknown"
        )
        values = {
            "source": project.source_channel if project else None,
            "topic": package.content_category if package else None,
            "duration": f"{int(clip.duration_seconds // 15) * 15}-{int(clip.duration_seconds // 15) * 15 + 14}s",
            "hook": str((package.fields_json if package else {}).get("primary_hook", "Unlabeled"))[:80],
            "posting_time": published_hour,
        }
        for key, value in values.items():
            label = value or "Unknown"
            bucket = groups[key].setdefault(label, {"posts": 0, "views": 0})
            bucket["posts"] = int(bucket["posts"]) + 1
            bucket["views"] = int(bucket["views"]) + (item.views or 0)
    recommendations = [f"Observe {key}: {max(values.items(), key=lambda pair: int(pair[1]['views']))[0]} currently has the most recorded views." for key, values in groups.items() if values]
    return {"published_posts": len(rows), "views": total_views, "average_retention_percentage": average_retention, "groups": groups, "recommendations": recommendations}
