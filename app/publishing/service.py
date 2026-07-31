"""Review-first provider boundary and explicit YouTube publishing workflow."""

import json
import os
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.brands.models import DestinationAccount
from app.common.config import Settings, get_settings
from app.common.errors import DomainError
from app.content_packages.models import ContentPackage, ContentPackageStatus
from app.ingestion.storage import LocalFilesystemStorage
from app.production.models import PostingQueueItem, ProductionClip, ProductionProject
from app.publishing.models import (
    PublishAttempt,
    PublishingAccountConnection,
    PublishRequest,
    PublishRequestStatus,
    PublishReviewGate,
)


class PublishingError(DomainError):
    def __init__(self, code: str, message: str) -> None:
        self.code, self.message = code, message
        super().__init__(message)


class FailureCategory:
    PRECONDITION = "PRECONDITION"
    VALIDATION = "VALIDATION"
    CREDENTIALS = "CREDENTIALS"
    AUTHORIZATION = "AUTHORIZATION"
    RATE_LIMIT = "RATE_LIMIT"
    TRANSIENT_NETWORK = "TRANSIENT_NETWORK"
    PROVIDER = "PROVIDER"
    CANCELLED = "CANCELLED"
    SAFETY = "SAFETY"


@dataclass(frozen=True)
class MediaValidation:
    path: Path
    mime_type: str
    duration_seconds: float


@dataclass(frozen=True)
class ProviderPublishResult:
    remote_post_id: str
    remote_post_url: str


class PublishingProvider(Protocol):
    provider_name: str

    def verify_connection(self, account: DestinationAccount) -> tuple[str | None, str | None]: ...
    def upload(self, account: DestinationAccount, media: MediaValidation, metadata: dict[str, object]) -> ProviderPublishResult: ...


class EnvironmentCredentialResolver:
    """Resolve an opaque ``env://NAME`` reference without persisting or logging its secret."""

    def resolve(self, reference: str | None) -> str:
        if not reference or not reference.startswith("env://"):
            raise PublishingError("CREDENTIAL_REFERENCE_REQUIRED", "an external env:// credential reference is required")
        name = reference.removeprefix("env://")
        if not name or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for char in name):
            raise PublishingError("INVALID_CREDENTIAL_REFERENCE", "credential reference is invalid")
        value = os.getenv(name)
        if not value:
            raise PublishingError("CREDENTIAL_UNAVAILABLE", "the referenced publishing credential is unavailable")
        return value


class YouTubeShortsOAuthProvider:
    """Official YouTube Data API resumable-upload provider. Only UNLISTED/PRIVATE are allowed."""

    provider_name = "YOUTUBE"

    def __init__(self, settings: Settings | None = None, resolver: EnvironmentCredentialResolver | None = None) -> None:
        self.settings = settings or get_settings()
        self.resolver = resolver or EnvironmentCredentialResolver()

    def _token(self, account: DestinationAccount) -> str:
        return self.resolver.resolve(account.credential_reference_id)

    def verify_connection(self, account: DestinationAccount) -> tuple[str | None, str | None]:
        token = self._token(account)
        try:
            response = httpx.get(
                "https://www.googleapis.com/youtube/v3/channels",
                params={"part": "id,snippet", "mine": "true"},
                headers={"Authorization": f"Bearer {token}"}, timeout=self.settings.publishing_http_timeout_seconds,
            )
        except httpx.HTTPError as error:
            raise PublishingError("CONNECTION_UNAVAILABLE", "could not reach YouTube to verify the connection") from error
        if response.status_code in {401, 403}:
            raise PublishingError("CONNECTION_AUTH_FAILED", "YouTube rejected the configured OAuth credential")
        if response.status_code >= 400:
            raise PublishingError("CONNECTION_PROVIDER_FAILED", "YouTube connection verification failed")
        items = response.json().get("items", [])
        if not items:
            raise PublishingError("CONNECTION_NO_CHANNEL", "the OAuth credential has no accessible YouTube channel")
        channel = items[0]
        return str(channel.get("id") or ""), str(channel.get("snippet", {}).get("customUrl") or "") or None

    def upload(self, account: DestinationAccount, media: MediaValidation, metadata: dict[str, object]) -> ProviderPublishResult:
        privacy = str(metadata.get("privacyStatus", "unlisted")).lower()
        if privacy not in {"private", "unlisted"}:
            raise PublishingError("PUBLIC_UPLOAD_FORBIDDEN", "this foundation only permits private or unlisted YouTube uploads")
        if not self.settings.publishing_enabled or not self.settings.publishing_youtube_enabled:
            raise PublishingError("PUBLISHING_DISABLED", "YouTube publishing is disabled by configuration")
        token = self._token(account)
        raw_tags = metadata.get("tags", [])
        tags = raw_tags[:15] if isinstance(raw_tags, list) else []
        body = {"snippet": {"title": str(metadata.get("title", "Untitled Short"))[:100], "description": str(metadata.get("description", ""))[:5000], "tags": tags}, "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}}
        try:
            start = httpx.post(
                "https://www.googleapis.com/upload/youtube/v3/videos",
                params={"uploadType": "resumable", "part": "snippet,status"}, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Type": media.mime_type, "X-Upload-Content-Length": str(media.path.stat().st_size)}, json=body, timeout=self.settings.publishing_http_timeout_seconds,
            )
            if start.status_code in {401, 403}:
                raise PublishingError("YOUTUBE_AUTH_FAILED", "YouTube rejected the OAuth credential or upload scope")
            if start.status_code == 429:
                raise PublishingError("YOUTUBE_RATE_LIMIT", "YouTube rate limit was reached")
            if start.status_code >= 400 or not start.headers.get("Location"):
                raise PublishingError("YOUTUBE_UPLOAD_INIT_FAILED", "YouTube did not accept the upload request")
            with media.path.open("rb") as handle:
                result = httpx.put(start.headers["Location"], content=handle, headers={"Content-Type": media.mime_type, "Content-Length": str(media.path.stat().st_size)}, timeout=self.settings.publishing_upload_timeout_seconds)
        except httpx.HTTPError as error:
            raise PublishingError("YOUTUBE_NETWORK_FAILURE", "network failure while uploading to YouTube") from error
        if result.status_code == 429:
            raise PublishingError("YOUTUBE_RATE_LIMIT", "YouTube rate limit was reached")
        if result.status_code >= 500:
            raise PublishingError("YOUTUBE_TRANSIENT_FAILURE", "YouTube temporarily failed the upload")
        if result.status_code >= 400:
            raise PublishingError("YOUTUBE_UPLOAD_FAILED", "YouTube rejected the uploaded media")
        video_id = str(result.json().get("id") or "")
        if not video_id:
            raise PublishingError("YOUTUBE_UPLOAD_FAILED", "YouTube did not return a remote video identifier")
        return ProviderPublishResult(video_id, f"https://www.youtube.com/watch?v={video_id}")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _provider(provider: str, settings: Settings | None = None) -> PublishingProvider:
    if provider.upper() == "YOUTUBE":
        return YouTubeShortsOAuthProvider(settings)
    raise PublishingError("UNSUPPORTED_PROVIDER", "only the official YouTube provider is available")


def _require_trusted_https_feature(settings: Settings | None = None) -> Settings:
    resolved = settings or get_settings()
    try:
        resolved.require_trusted_https_feature()
    except ValueError as error:
        raise PublishingError("TRUSTED_HTTPS_REQUIRED", str(error)) from error
    return resolved


def verify_destination_connection(session: Session, actor_id: uuid.UUID, account: DestinationAccount, settings: Settings | None = None) -> PublishingAccountConnection:
    settings = _require_trusted_https_feature(settings)
    if account.provider.upper() != "YOUTUBE":
        raise PublishingError("UNSUPPORTED_PROVIDER", "only YouTube destination accounts are supported")
    connection = session.scalar(select(PublishingAccountConnection).where(PublishingAccountConnection.destination_account_id == account.id))
    if connection is None:
        connection = PublishingAccountConnection(destination_account_id=account.id)
        session.add(connection)
    try:
        channel_id, channel_url = _provider(account.provider, settings).verify_connection(account)
    except PublishingError as error:
        connection.connection_state, connection.last_error_category, connection.last_error_summary, connection.checked_at = "ERROR", error.code, error.message, _now()
    else:
        connection.connection_state, connection.provider_account_id, connection.provider_channel_url, connection.last_error_category, connection.last_error_summary, connection.checked_at = "CONNECTED", channel_id, channel_url, None, None, _now()
    session.add(AuditEvent(actor_id=actor_id, entity_type="destination_account", entity_id=account.id, brand_id=account.brand_id, event_name="publishing.connection.checked", payload={"state": connection.connection_state}))
    session.commit()
    return connection


def set_review_gate(session: Session, actor_id: uuid.UUID, clip: ProductionClip, rights_required: bool, rights_disposition: str, moderation_disposition: str, notes: str | None = None) -> PublishReviewGate:
    if rights_disposition not in {"APPROVED", "REJECTED", "NOT_APPLICABLE"} or moderation_disposition not in {"APPROVED", "REJECTED", "PENDING"}:
        raise PublishingError("INVALID_REVIEW_GATE", "invalid rights or moderation disposition")
    gate = session.scalar(select(PublishReviewGate).where(PublishReviewGate.clip_id == clip.id))
    if gate is None:
        gate = PublishReviewGate(clip_id=clip.id, brand_id=clip.brand_id)
    gate.rights_required, gate.rights_disposition, gate.moderation_disposition, gate.notes = rights_required, rights_disposition, moderation_disposition, notes
    gate.rights_reviewer_id = actor_id if rights_required else None
    gate.moderation_reviewer_id = actor_id if moderation_disposition != "PENDING" else None
    session.add(gate)
    session.add(AuditEvent(actor_id=actor_id, entity_type="production_clip", entity_id=clip.id, brand_id=clip.brand_id, event_name="publishing.review_gate.updated", payload={"rights_required": rights_required, "rights": rights_disposition, "moderation": moderation_disposition}))
    session.commit()
    return gate


def _assert_preconditions(session: Session, clip: ProductionClip, package: ContentPackage, destination: DestinationAccount) -> tuple[ProductionProject, PostingQueueItem]:
    project = session.get(ProductionProject, clip.project_id)
    queue = session.scalar(select(PostingQueueItem).where(PostingQueueItem.clip_id == clip.id))
    if project is None or queue is None:
        raise PublishingError("QUEUE_REQUIRED", "an approved clip must be in the posting queue")
    if clip.render_status != "SUCCEEDED" or clip.approval_status != "APPROVED":
        raise PublishingError("CLIP_APPROVAL_REQUIRED", "a successfully rendered, approved clip is required")
    if package.clip_id != clip.id or package.status != ContentPackageStatus.APPROVED:
        raise PublishingError("CONTENT_PACKAGE_APPROVAL_REQUIRED", "an approved content package for this clip is required")
    if destination.brand_id != clip.brand_id or not destination.is_active or destination.provider.upper() != "YOUTUBE":
        raise PublishingError("DESTINATION_ACCOUNT_INVALID", "an active YouTube destination account for this brand is required")
    accepted = session.scalar(select(AuditEvent.id).where(AuditEvent.entity_id == project.id, AuditEvent.event_name == "production.source.accepted"))
    if accepted is None:
        raise PublishingError("SOURCE_ACCEPTANCE_REQUIRED", "the project source must be explicitly accepted before publishing")
    gate = session.scalar(select(PublishReviewGate).where(PublishReviewGate.clip_id == clip.id))
    if gate is None or gate.moderation_disposition != "APPROVED":
        raise PublishingError("MODERATION_APPROVAL_REQUIRED", "explicit moderation approval is required before publishing")
    if gate.rights_required and gate.rights_disposition != "APPROVED":
        raise PublishingError("RIGHTS_APPROVAL_REQUIRED", "explicit rights approval is required before publishing")
    return project, queue


def metadata_for_youtube(package: ContentPackage) -> dict[str, object]:
    fields = package.fields_json
    tags = fields.get("hashtags", [])
    if isinstance(tags, str):
        tags = [tag.lstrip("#") for tag in tags.split() if tag.startswith("#")]
    return {"title": fields.get("youtube_shorts_title") or fields.get("primary_hook") or "Untitled Short", "description": fields.get("description") or fields.get("source_attribution_text") or "", "tags": tags if isinstance(tags, list) else [], "privacyStatus": "unlisted"}


def request_publish(session: Session, actor_id: uuid.UUID, clip: ProductionClip, package: ContentPackage, destination: DestinationAccount, idempotency_key: str, decision_type: str, scheduled_for: datetime | None = None, settings: Settings | None = None) -> PublishRequest:
    _require_trusted_https_feature(settings)
    if decision_type not in {"MANUAL", "SCHEDULED"}:
        raise PublishingError("INVALID_PUBLISH_DECISION", "publish decision must be MANUAL or SCHEDULED")
    existing = session.scalar(select(PublishRequest).where(PublishRequest.idempotency_key == idempotency_key))
    if existing is not None:
        if existing.clip_id != clip.id or existing.destination_account_id != destination.id:
            raise PublishingError("IDEMPOTENCY_CONFLICT", "idempotency key belongs to another publish request")
        return existing
    _, queue = _assert_preconditions(session, clip, package, destination)
    if decision_type == "SCHEDULED" and (scheduled_for is None or scheduled_for <= datetime.now(UTC)):
        raise PublishingError("SCHEDULE_REQUIRED", "a future schedule is required for scheduled publishing")
    request = PublishRequest(brand_id=clip.brand_id, queue_item_id=queue.id, clip_id=clip.id, content_package_id=package.id, destination_account_id=destination.id, requested_by_id=actor_id, decision_type=decision_type, idempotency_key=idempotency_key, scheduled_for=scheduled_for.isoformat() if scheduled_for else None, platform_metadata=metadata_for_youtube(package))
    session.add(request)
    session.flush()
    session.add(AuditEvent(actor_id=actor_id, entity_type="publish_request", entity_id=request.id, brand_id=clip.brand_id, event_name="publishing.request.created", payload={"decision_type": decision_type, "destination_account_id": str(destination.id)}))
    session.commit()
    return request


def confirm_publish(session: Session, actor_id: uuid.UUID, request: PublishRequest, settings: Settings | None = None) -> PublishRequest:
    _require_trusted_https_feature(settings)
    if request.status != PublishRequestStatus.AWAITING_CONFIRMATION:
        return request
    if request.cancelled_before_upload:
        raise PublishingError("PUBLISH_CANCELLED", "the publishing request was cancelled before upload")
    request.confirmed_by_id, request.confirmed_at = actor_id, _now()
    if request.decision_type == "SCHEDULED":
        request.status = PublishRequestStatus.SCHEDULED
    else:
        request.status = PublishRequestStatus.QUEUED
    session.add(AuditEvent(actor_id=actor_id, entity_type="publish_request", entity_id=request.id, brand_id=request.brand_id, event_name="publishing.request.confirmed", payload={"status": request.status}))
    session.commit()
    return request


def cancel_publish(session: Session, actor_id: uuid.UUID, request: PublishRequest) -> PublishRequest:
    if request.status == PublishRequestStatus.UPLOADING:
        raise PublishingError("UPLOAD_ALREADY_STARTED", "an upload already started and cannot be cancelled safely")
    if request.status in {PublishRequestStatus.SUCCEEDED, PublishRequestStatus.FAILED}:
        raise PublishingError("PUBLISH_FINALIZED", "a finalized publishing request cannot be cancelled")
    request.status, request.cancelled_before_upload = PublishRequestStatus.CANCELLED, True
    session.add(AuditEvent(actor_id=actor_id, entity_type="publish_request", entity_id=request.id, brand_id=request.brand_id, event_name="publishing.request.cancelled"))
    session.commit()
    return request


def _media_validation(clip: ProductionClip, settings: Settings) -> MediaValidation:
    if not clip.storage_key:
        raise PublishingError("MEDIA_UNAVAILABLE", "the rendered clip has no stored media")
    storage = LocalFilesystemStorage(Path(settings.local_storage_root))
    if not storage.exists(clip.storage_key):
        raise PublishingError("MEDIA_UNAVAILABLE", "the rendered clip is unavailable from storage")
    path = storage._path(clip.storage_key, storage.assets_root)
    try:
        probe = subprocess.run([settings.ffprobe_path, "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(path)], capture_output=True, text=True, timeout=settings.publishing_media_probe_timeout_seconds, check=True)
        result = json.loads(probe.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        raise PublishingError("MEDIA_VALIDATION_FAILED", "ffprobe could not validate the rendered clip") from error
    stream_types = {stream.get("codec_type") for stream in result.get("streams", [])}
    duration = float(result.get("format", {}).get("duration") or 0)
    if "video" not in stream_types or duration <= 0 or duration > 180:
        raise PublishingError("MEDIA_VALIDATION_FAILED", "the rendered media is not a supported short-form video")
    return MediaValidation(path, "video/mp4", duration)


def _failure_category(error: PublishingError) -> str:
    if "RATE_LIMIT" in error.code:
        return FailureCategory.RATE_LIMIT
    if "NETWORK" in error.code or "TRANSIENT" in error.code or "UNAVAILABLE" in error.code:
        return FailureCategory.TRANSIENT_NETWORK
    if "AUTH" in error.code or "CREDENTIAL" in error.code:
        return FailureCategory.CREDENTIALS
    if "VALIDATION" in error.code or "MEDIA" in error.code:
        return FailureCategory.VALIDATION
    if "CANCEL" in error.code:
        return FailureCategory.CANCELLED
    return FailureCategory.PRECONDITION


def execute_publish(session: Session, request_id: uuid.UUID, settings: Settings | None = None) -> PublishRequest:
    settings = settings or get_settings()
    request = session.get(PublishRequest, request_id)
    if request is None:
        raise PublishingError("PUBLISH_REQUEST_NOT_FOUND", "publishing request was not found")
    if request.status in {PublishRequestStatus.SUCCEEDED, PublishRequestStatus.CANCELLED, PublishRequestStatus.UPLOADING}:
        return request
    if request.status == PublishRequestStatus.SCHEDULED and request.scheduled_for and datetime.fromisoformat(request.scheduled_for) > datetime.now(UTC):
        return request
    if request.status not in {PublishRequestStatus.QUEUED, PublishRequestStatus.SCHEDULED, PublishRequestStatus.FAILED}:
        raise PublishingError("PUBLISH_NOT_CONFIRMED", "a human confirmation is required before upload")
    clip, package, destination = session.get(ProductionClip, request.clip_id), session.get(ContentPackage, request.content_package_id), session.get(DestinationAccount, request.destination_account_id)
    if clip is None or package is None or destination is None:
        raise PublishingError("PUBLISH_RECORD_MISSING", "publishing prerequisites no longer exist")
    _assert_preconditions(session, clip, package, destination)
    request.status, request.upload_progress_percent, request.attempt_count = PublishRequestStatus.UPLOADING, 5, request.attempt_count + 1
    attempt = PublishAttempt(publish_request_id=request.id, attempt_number=request.attempt_count, status="STARTED")
    session.add_all([request, attempt])
    session.commit()
    try:
        media = _media_validation(clip, settings)
        request.upload_progress_percent = 25
        session.commit()
        result = _provider(destination.provider, settings).upload(destination, media, request.platform_metadata)
    except PublishingError as error:
        category = _failure_category(error)
        request.status, request.failure_category, request.failure_summary, request.upload_progress_percent = PublishRequestStatus.FAILED, category, error.message, 0
        if category in {FailureCategory.RATE_LIMIT, FailureCategory.TRANSIENT_NETWORK} and request.attempt_count < settings.publishing_max_attempts:
            request.next_attempt_at = (datetime.now(UTC) + timedelta(seconds=settings.publishing_retry_backoff_seconds * (2 ** (request.attempt_count - 1)))).isoformat()
        attempt.status, attempt.failure_category, attempt.detail = "FAILED", category, error.message
        session.add(AuditEvent(actor_id=request.confirmed_by_id, entity_type="publish_request", entity_id=request.id, brand_id=request.brand_id, event_name="publishing.request.failed", payload={"category": category}))
        session.commit()
        return request
    request.status, request.upload_progress_percent, request.remote_post_id, request.remote_post_url, request.failure_category, request.failure_summary = PublishRequestStatus.SUCCEEDED, 100, result.remote_post_id, result.remote_post_url, None, None
    attempt.status, attempt.remote_post_id, attempt.remote_post_url = "SUCCEEDED", result.remote_post_id, result.remote_post_url
    queue = session.get(PostingQueueItem, request.queue_item_id)
    if queue:
        queue.status, queue.attempts, queue.published_platform_id, queue.published_url = "PUBLISHED", request.attempt_count, result.remote_post_id, result.remote_post_url
    clip.publication_status = "PUBLISHED"
    session.add(AuditEvent(actor_id=request.confirmed_by_id, entity_type="publish_request", entity_id=request.id, brand_id=request.brand_id, event_name="publishing.request.succeeded", payload={"remote_post_id": result.remote_post_id}))
    session.commit()
    return request
