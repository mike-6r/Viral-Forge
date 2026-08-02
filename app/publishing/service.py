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
    video_codec: str | None = None
    audio_codec: str | None = None
    width: int | None = None
    height: int | None = None
    frame_rate: float | None = None


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
    if provider.upper() == "TIKTOK":
        from app.publishing.tiktok import TikTokPublishingProvider

        return TikTokPublishingProvider(settings)  # type: ignore[return-value]
    raise PublishingError("UNSUPPORTED_PROVIDER", "unsupported publishing provider")


def _require_trusted_https_feature(settings: Settings | None = None) -> Settings:
    resolved = settings or get_settings()
    try:
        resolved.require_trusted_https_feature()
    except ValueError as error:
        raise PublishingError("TRUSTED_HTTPS_REQUIRED", str(error)) from error
    return resolved


def verify_destination_connection(session: Session, actor_id: uuid.UUID, account: DestinationAccount, settings: Settings | None = None) -> PublishingAccountConnection:
    settings = _require_trusted_https_feature(settings)
    if account.provider.upper() not in {"YOUTUBE", "TIKTOK"}:
        raise PublishingError("UNSUPPORTED_PROVIDER", "unsupported publishing provider")
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
    if destination.brand_id != clip.brand_id or not destination.is_active or destination.provider.upper() not in {"YOUTUBE", "TIKTOK"}:
        raise PublishingError("DESTINATION_ACCOUNT_INVALID", "an active destination account for this brand is required")
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


def metadata_for_tiktok(package: ContentPackage, mode: str, privacy_level: str | None = None) -> dict[str, object]:
    fields = package.fields_json
    tags = fields.get("hashtags", [])
    if isinstance(tags, str):
        tags = [tag for tag in tags.split() if tag.startswith("#")]
    caption = str(fields.get("tiktok_caption") or fields.get("primary_hook") or "")
    if isinstance(tags, list):
        caption = f"{caption} {' '.join(str(tag) for tag in tags)}".strip()
    return {
        "caption": caption[:2200],
        "attribution": fields.get("source_attribution_text") or "",
        "mode": mode,
        "privacy_level": privacy_level or "SELF_ONLY",
        "disable_comment": False,
        "disable_duet": False,
        "disable_stitch": False,
    }


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
    if destination.provider.upper() == "TIKTOK":
        raise PublishingError("TIKTOK_MODE_REQUIRED", "use the TikTok request flow and explicitly choose draft upload or Direct Post")
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
    if request.status in {PublishRequestStatus.UPLOADING, PublishRequestStatus.INITIALIZING, PublishRequestStatus.TRANSFERRING, PublishRequestStatus.PROCESSING}:
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
        probe = subprocess.run([settings.ffprobe_path, "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,width,height,r_frame_rate", "-of", "json", str(path)], capture_output=True, text=True, timeout=settings.publishing_media_probe_timeout_seconds, check=True)
        result = json.loads(probe.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        raise PublishingError("MEDIA_VALIDATION_FAILED", "ffprobe could not validate the rendered clip") from error
    streams = result.get("streams", [])
    stream_types = {stream.get("codec_type") for stream in streams}
    duration = float(result.get("format", {}).get("duration") or 0)
    if "video" not in stream_types or duration <= 0 or duration > 180:
        raise PublishingError("MEDIA_VALIDATION_FAILED", "the rendered media is not a supported short-form video")
    video: dict[str, object] = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    audio: dict[str, object] = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
    frame_rate = None
    try:
        numerator, denominator = str(video.get("r_frame_rate") or "0/1").split("/", 1)
        frame_rate = float(numerator) / float(denominator) if float(denominator) else None
    except (TypeError, ValueError):
        frame_rate = None
    return MediaValidation(path, "video/mp4", duration, str(video.get("codec_name") or "") or None, str(audio.get("codec_name") or "") or None, int(str(video["width"])) if str(video.get("width") or "").isdigit() else None, int(str(video["height"])) if str(video.get("height") or "").isdigit() else None, frame_rate)


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
    if destination.provider.upper() == "TIKTOK":
        return execute_tiktok_publish(session, request_id, settings)
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


def _tiktok_settings(settings: Settings) -> None:
    if not settings.tiktok_enabled:
        raise PublishingError("TIKTOK_DISABLED", "TikTok publishing is disabled by configuration")
    if settings.tiktok_emergency_pause:
        raise PublishingError("TIKTOK_EMERGENCY_PAUSE", "TikTok transfers are paused by the operator")


def request_tiktok_publish(
    session: Session,
    actor_id: uuid.UUID,
    clip: ProductionClip,
    package: ContentPackage,
    destination: DestinationAccount,
    idempotency_key: str,
    mode: str,
    privacy_level: str | None = None,
    settings: Settings | None = None,
) -> PublishRequest:
    """Create one explicitly unconfirmed TikTok transfer request.

    The idempotency boundary includes the approved package generation and mode,
    so an operator cannot accidentally transfer the same creative twice.
    """
    settings = _require_trusted_https_feature(settings)
    _tiktok_settings(settings)
    from app.publishing.tiktok import TikTokMode

    if mode not in {TikTokMode.DRAFT_UPLOAD, TikTokMode.DIRECT_POST}:
        raise PublishingError("TIKTOK_MODE_INVALID", "TikTok mode must be DRAFT_UPLOAD or DIRECT_POST")
    if mode == TikTokMode.DRAFT_UPLOAD and not settings.tiktok_draft_upload_enabled:
        raise PublishingError("TIKTOK_DRAFT_DISABLED", "TikTok draft upload is disabled")
    if mode == TikTokMode.DIRECT_POST and not settings.tiktok_direct_post_enabled:
        raise PublishingError("TIKTOK_DIRECT_POST_DISABLED", "TikTok Direct Post is disabled")
    if destination.provider.upper() != "TIKTOK" or destination.brand_id != clip.brand_id:
        raise PublishingError("TIKTOK_DESTINATION_INVALID", "TikTok destination must be active and owned by the clip brand")
    if mode == TikTokMode.DIRECT_POST and settings.tiktok_application_review_state != "AUDITED":
        if privacy_level not in {None, "SELF_ONLY"}:
            raise PublishingError("TIKTOK_PUBLIC_POST_FORBIDDEN", "public TikTok Direct Post is unavailable until the TikTok app is audited")
        privacy_level = "SELF_ONLY"
    connection = session.scalar(select(PublishingAccountConnection).where(PublishingAccountConnection.destination_account_id == destination.id))
    if connection is None or connection.connection_state != "CONNECTED":
        raise PublishingError("TIKTOK_CONNECTION_REQUIRED", "a verified TikTok account connection is required")
    if connection.provider_account_id and connection.provider_account_id != destination.account_reference:
        raise PublishingError("TIKTOK_CREATOR_IDENTITY_MISMATCH", "connected TikTok creator does not match the destination account identity")
    existing = session.scalar(select(PublishRequest).where(PublishRequest.idempotency_key == idempotency_key))
    if existing is not None:
        if existing.clip_id != clip.id or existing.destination_account_id != destination.id or existing.provider_mode != mode:
            raise PublishingError("IDEMPOTENCY_CONFLICT", "idempotency key belongs to another publishing request")
        return existing
    duplicate = session.scalar(select(PublishRequest).where(PublishRequest.clip_id == clip.id, PublishRequest.destination_account_id == destination.id, PublishRequest.provider_mode == mode, PublishRequest.content_package_generation_version == package.generation_version).order_by(PublishRequest.created_at.desc()))
    if duplicate is not None:
        return duplicate
    _, queue = _assert_preconditions(session, clip, package, destination)
    day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    today = session.scalar(select(PublishRequest.id).where(PublishRequest.destination_account_id == destination.id, PublishRequest.created_at >= day_start).limit(settings.tiktok_max_transfers_per_day))
    if today is not None:
        count = len(list(session.scalars(select(PublishRequest.id).where(PublishRequest.destination_account_id == destination.id, PublishRequest.created_at >= day_start))))
        if count >= settings.tiktok_max_transfers_per_day:
            raise PublishingError("TIKTOK_DAILY_LIMIT", "TikTok daily transfer limit reached for this destination")
    pending_drafts = len(
        list(
            session.scalars(
                select(PublishRequest.id).where(
                    PublishRequest.destination_account_id == destination.id,
                    PublishRequest.provider_mode == TikTokMode.DRAFT_UPLOAD,
                    PublishRequest.status == PublishRequestStatus.OPERATOR_COMPLETION_REQUIRED,
                )
            )
        )
    )
    if mode == TikTokMode.DRAFT_UPLOAD and pending_drafts >= settings.tiktok_max_pending_drafts:
        raise PublishingError("TIKTOK_PENDING_DRAFT_LIMIT", "TikTok destination has the maximum number of pending drafts")
    latest = session.scalar(
        select(PublishRequest)
        .where(
            PublishRequest.destination_account_id == destination.id,
            PublishRequest.status.not_in([PublishRequestStatus.CANCELLED, PublishRequestStatus.FAILED]),
        )
        .order_by(PublishRequest.created_at.desc())
    )
    if latest is not None and latest.created_at:
        elapsed = (datetime.now(UTC) - latest.created_at).total_seconds()
        if elapsed < settings.tiktok_minimum_transfer_interval_seconds:
            raise PublishingError("TIKTOK_MINIMUM_INTERVAL", "TikTok destination transfer interval has not elapsed")
    metadata = metadata_for_tiktok(package, mode, privacy_level)
    request = PublishRequest(brand_id=clip.brand_id, queue_item_id=queue.id, clip_id=clip.id, content_package_id=package.id, destination_account_id=destination.id, requested_by_id=actor_id, decision_type="MANUAL", idempotency_key=idempotency_key, platform_metadata=metadata, provider_mode=mode, provider_settings={"application_review_state": settings.tiktok_application_review_state}, content_package_generation_version=package.generation_version)
    session.add(request)
    session.flush()
    session.add(AuditEvent(actor_id=actor_id, entity_type="publish_request", entity_id=request.id, brand_id=clip.brand_id, event_name="tiktok.request.created", payload={"mode": mode, "destination_account_id": str(destination.id)}))
    session.commit()
    return request


def _validate_tiktok_media(media: MediaValidation, maximum_duration: int | None, settings: Settings) -> None:
    if media.path.suffix.lower() != ".mp4" or media.video_codec not in {"h264", "hevc"} or not media.audio_codec:
        raise PublishingError("TIKTOK_MEDIA_UNSUPPORTED", "TikTok requires a full-quality MP4 with a supported video and audio codec")
    if not media.width or not media.height or min(media.width, media.height) < 360 or max(media.width, media.height) > 4096:
        raise PublishingError("TIKTOK_MEDIA_DIMENSIONS_INVALID", "rendered video dimensions are unsupported by TikTok")
    if not media.frame_rate or not 23 <= media.frame_rate <= 60:
        raise PublishingError("TIKTOK_MEDIA_FRAME_RATE_INVALID", "rendered video frame rate is unsupported by TikTok")
    if maximum_duration and media.duration_seconds > maximum_duration:
        raise PublishingError("TIKTOK_DURATION_EXCEEDED", "clip duration exceeds the connected creator's TikTok limit")
    if media.path.stat().st_size > settings.tiktok_max_media_bytes:
        raise PublishingError("TIKTOK_MEDIA_TOO_LARGE", "rendered clip exceeds the configured TikTok limit")


def execute_tiktok_publish(session: Session, request_id: uuid.UUID, settings: Settings | None = None) -> PublishRequest:
    settings = settings or get_settings()
    _tiktok_settings(settings)
    from app.publishing.tiktok import TikTokMode, TikTokPublishingProvider, persist_capabilities

    request = session.get(PublishRequest, request_id)
    if request is None:
        raise PublishingError("PUBLISH_REQUEST_NOT_FOUND", "publishing request was not found")
    if request.status in {PublishRequestStatus.CANCELLED, PublishRequestStatus.SUCCEEDED, PublishRequestStatus.OPERATOR_COMPLETION_REQUIRED, PublishRequestStatus.UNKNOWN_REMOTE_OUTCOME, PublishRequestStatus.MANUAL_RECONCILIATION_REQUIRED}:
        return request
    if request.status not in {PublishRequestStatus.QUEUED, PublishRequestStatus.FAILED}:
        raise PublishingError("PUBLISH_NOT_CONFIRMED", "a separate human confirmation is required before transfer")
    clip, package, destination = session.get(ProductionClip, request.clip_id), session.get(ContentPackage, request.content_package_id), session.get(DestinationAccount, request.destination_account_id)
    if clip is None or package is None or destination is None:
        raise PublishingError("PUBLISH_RECORD_MISSING", "TikTok publishing prerequisites no longer exist")
    _assert_preconditions(session, clip, package, destination)
    provider = TikTokPublishingProvider(settings)
    request.status, request.attempt_count = PublishRequestStatus.INITIALIZING, request.attempt_count + 1
    attempt = PublishAttempt(publish_request_id=request.id, attempt_number=request.attempt_count, status="STARTED")
    session.add(attempt)
    session.commit()
    try:
        capability = None
        if request.provider_mode == TikTokMode.DIRECT_POST:
            capability = provider.creator_info(destination)
            stored = persist_capabilities(session, destination, capability)
            if stored.creator_identity_reference != capability.creator_identity_reference:
                raise PublishingError("TIKTOK_CREATOR_IDENTITY_CHANGED", "TikTok creator identity changed before transfer")
        media = _media_validation(clip, settings)
        _validate_tiktok_media(media, capability.max_video_duration_seconds if capability else None, settings)
        initialized = provider.initialize(destination, request.provider_mode or TikTokMode.DRAFT_UPLOAD, media, request.platform_metadata, capability)
        request.provider_upload_session_id, request.remote_post_id = initialized.publish_id, initialized.publish_id
        request.status, request.transfer_started_at, request.upload_progress_percent = PublishRequestStatus.TRANSFERRING, _now(), 1
        session.commit()
        provider.transfer(initialized, media, lambda value: _persist_tiktok_progress(session, request, value))
    except PublishingError as error:
        unknown = (
            request.provider_upload_session_id is not None
            and request.status == PublishRequestStatus.TRANSFERRING
            and error.code in {"TIKTOK_NETWORK_FAILURE", "TIKTOK_UNKNOWN_REMOTE_OUTCOME"}
        )
        request.status = PublishRequestStatus.UNKNOWN_REMOTE_OUTCOME if unknown else PublishRequestStatus.FAILED
        request.failure_category, request.failure_summary = _failure_category(error), error.message
        request.reconciliation_reason = "transfer network outcome is uncertain" if unknown else None
        attempt.status, attempt.failure_category, attempt.detail = "UNKNOWN_REMOTE_OUTCOME" if unknown else "FAILED", request.failure_category, error.message
        session.add(AuditEvent(actor_id=request.confirmed_by_id, entity_type="publish_request", entity_id=request.id, brand_id=request.brand_id, event_name="tiktok.request.uncertain" if unknown else "tiktok.request.failed", payload={"category": request.failure_category}))
        session.commit()
        return request
    request.upload_progress_percent = 100
    if request.provider_mode == TikTokMode.DRAFT_UPLOAD:
        request.status, request.operator_completion_state = PublishRequestStatus.OPERATOR_COMPLETION_REQUIRED, "INBOX_EDIT_AND_POST_REQUIRED"
        attempt.status = "DRAFT_TRANSFERRED"
        session.add(AuditEvent(actor_id=request.confirmed_by_id, entity_type="publish_request", entity_id=request.id, brand_id=request.brand_id, event_name="tiktok.draft.operator_completion_required"))
    else:
        request.status, attempt.status = PublishRequestStatus.PROCESSING, "PROCESSING"
    session.commit()
    return request


def _persist_tiktok_progress(session: Session, request: PublishRequest, value: int) -> None:
    request.upload_progress_percent = value
    session.commit()


def refresh_tiktok_status(session: Session, request_id: uuid.UUID, settings: Settings | None = None) -> PublishRequest:
    settings = settings or get_settings()
    from app.publishing.tiktok import TikTokPublishingProvider

    request = session.get(PublishRequest, request_id)
    if request is None or not request.remote_post_id:
        raise PublishingError("TIKTOK_STATUS_UNAVAILABLE", "TikTok request has no remote publish identifier")
    if request.status not in {PublishRequestStatus.PROCESSING, PublishRequestStatus.UNKNOWN_REMOTE_OUTCOME, PublishRequestStatus.MANUAL_RECONCILIATION_REQUIRED}:
        return request
    destination = session.get(DestinationAccount, request.destination_account_id)
    if destination is None:
        raise PublishingError("PUBLISH_RECORD_MISSING", "TikTok destination account no longer exists")
    remote, uploaded, post_id, reason = TikTokPublishingProvider(settings).status(destination, request.remote_post_id)
    request.provider_remote_status = remote
    if uploaded is not None:
        request.upload_progress_percent = max(request.upload_progress_percent, min(100, int(uploaded)))
    if remote == "PUBLISH_COMPLETE":
        request.status, request.remote_post_id, request.remote_post_url = PublishRequestStatus.SUCCEEDED, post_id or request.remote_post_id, request.remote_post_url
    elif remote == "FAILED":
        request.status, request.failure_summary = PublishRequestStatus.FAILED, reason or "TikTok processing failed"
    elif request.attempt_count >= settings.tiktok_max_status_poll_attempts:
        request.status, request.reconciliation_reason = PublishRequestStatus.MANUAL_RECONCILIATION_REQUIRED, "TikTok status polling limit reached"
    session.commit()
    return request


def refresh_tiktok_connection(
    session: Session, actor_id: uuid.UUID | None, account_id: uuid.UUID, settings: Settings | None = None
) -> PublishingAccountConnection:
    """Refresh one TikTok token under a destination-row lock and atomically replace it."""
    settings = settings or get_settings()
    from app.publishing.credentials import credential_store
    from app.publishing.tiktok import TikTokPublishingProvider

    account = session.scalar(select(DestinationAccount).where(DestinationAccount.id == account_id).with_for_update())
    if account is None or account.provider.upper() != "TIKTOK" or not account.credential_reference_id:
        raise PublishingError("TIKTOK_DESTINATION_INVALID", "an active TikTok destination is required")
    connection = session.scalar(
        select(PublishingAccountConnection).where(
            PublishingAccountConnection.destination_account_id == account.id
        )
    )
    if connection is None:
        connection = PublishingAccountConnection(destination_account_id=account.id)
    try:
        tokens = TikTokPublishingProvider(settings).refresh_token(account)
        credential_store(settings).replace(account.credential_reference_id, tokens.payload())
        connection.connection_state = "CONNECTED"
        connection.granted_scopes = sorted(tokens.scopes)
        connection.credential_expires_at = str(tokens.payload().get("expires_at") or "") or None
        connection.last_error_category, connection.last_error_summary = None, None
    except PublishingError as error:
        connection.connection_state = "DEGRADED"
        connection.last_error_category, connection.last_error_summary = error.code, error.message
        session.add(connection)
        session.commit()
        raise
    connection.checked_at = _now()
    session.add(connection)
    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="destination_account",
            entity_id=account.id,
            brand_id=account.brand_id,
            event_name="tiktok.credential.refreshed",
        )
    )
    session.commit()
    return connection


def disconnect_tiktok_connection(
    session: Session, actor_id: uuid.UUID, account: DestinationAccount, settings: Settings | None = None
) -> PublishingAccountConnection:
    """Revoke first, then remove the external token and deactivate the destination."""
    settings = settings or get_settings()
    from app.publishing.credentials import credential_store
    from app.publishing.tiktok import TikTokPublishingProvider

    if account.provider.upper() != "TIKTOK" or not account.credential_reference_id:
        raise PublishingError("TIKTOK_DESTINATION_INVALID", "TikTok destination credential is required")
    connection = session.scalar(
        select(PublishingAccountConnection).where(
            PublishingAccountConnection.destination_account_id == account.id
        )
    )
    if connection is None:
        connection = PublishingAccountConnection(destination_account_id=account.id)
    try:
        TikTokPublishingProvider(settings).revoke(account)
        credential_store(settings).delete(account.credential_reference_id)
    except PublishingError as error:
        connection.connection_state = "DEGRADED"
        connection.last_error_category, connection.last_error_summary = error.code, error.message
        session.add(connection)
        session.commit()
        raise
    account.is_active = False
    connection.connection_state = "DISCONNECTED"
    connection.granted_scopes, connection.credential_expires_at = [], None
    connection.last_error_category, connection.last_error_summary, connection.checked_at = None, None, _now()
    session.add_all([account, connection])
    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="destination_account",
            entity_id=account.id,
            brand_id=account.brand_id,
            event_name="tiktok.connection.disconnected",
        )
    )
    session.commit()
    return connection


def complete_tiktok_draft(session: Session, actor_id: uuid.UUID, request: PublishRequest, outcome: str, post_url: str | None = None) -> PublishRequest:
    if request.provider_mode != "DRAFT_UPLOAD" or request.status != PublishRequestStatus.OPERATOR_COMPLETION_REQUIRED:
        raise PublishingError("TIKTOK_DRAFT_NOT_AWAITING_COMPLETION", "TikTok draft is not awaiting operator completion")
    if outcome not in {"POSTED", "REJECTED", "ABANDONED"}:
        raise PublishingError("TIKTOK_DRAFT_OUTCOME_INVALID", "TikTok draft outcome is invalid")
    request.operator_completion_state = outcome
    if outcome == "POSTED":
        request.status, request.remote_post_url = PublishRequestStatus.SUCCEEDED, post_url
    else:
        request.status = PublishRequestStatus.CANCELLED
    session.add(AuditEvent(actor_id=actor_id, entity_type="publish_request", entity_id=request.id, brand_id=request.brand_id, event_name="tiktok.draft.completed", payload={"outcome": outcome}))
    session.commit()
    return request
