"""Private preview grants, byte streaming helpers, and conservative retention.

This module deliberately accepts database identifiers, never client supplied storage
keys or paths.  The storage provider remains the sole authority for file access.
"""

import hashlib
import hmac
import secrets
import shutil
import subprocess
import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.accounts.models import User  # noqa: F401
from app.audit.models import AuditEvent
from app.common.config import Settings, get_settings
from app.content.models import MediaAsset
from app.ingestion.storage import LocalFilesystemStorage
from app.media_preview.models import PreviewGrant
from app.production.models import ProductionClip, ProductionProject
from app.publishing.models import PublishRequest, PublishRequestStatus
from app.sources.models import Source  # noqa: F401

# Register the legacy ingestion source table before MediaAsset's optional
# source_id foreign key is flushed by a production-only media workflow.


class PreviewError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class IssuedPreview:
    grant: PreviewGrant
    url: str
    raw_token: str
    reused: bool = False


@dataclass(frozen=True)
class CleanupResult:
    selected: int
    deleted: int
    reclaimed_bytes: int
    failures: int
    dry_run: bool


_cleanup_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(UTC)


def _token_digest(token: str, settings: Settings) -> str:
    return hmac.new(
        settings.preview_hashing_secret.encode("utf-8"), token.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _deadline(settings: Settings, asset_type: str, clip: ProductionClip | None = None) -> datetime:
    seconds = settings.preview_retention_unreviewed_seconds
    if asset_type == "PREVIEW_PROXY":
        seconds = settings.preview_retention_proxy_seconds
    elif asset_type == "SOURCE_VIDEO":
        seconds = settings.preview_retention_source_seconds
    elif asset_type in {"TEMPORARY_PROCESSING_FILE", "TRANSCRIPTION_TEMP_FILE"}:
        seconds = settings.preview_retention_temporary_seconds
    elif clip and clip.approval_status == "REJECTED":
        seconds = settings.preview_retention_rejected_seconds
    elif clip and clip.approval_status == "APPROVED":
        seconds = settings.preview_retention_approved_seconds
    return _now() + timedelta(seconds=seconds)


def ensure_clip_asset(
    session: Session,
    clip: ProductionClip,
    storage: LocalFilesystemStorage,
    settings: Settings | None = None,
) -> MediaAsset:
    """Link a rendered clip once; existing rows are reused rather than duplicated."""
    settings = settings or get_settings()
    asset = session.scalar(
        select(MediaAsset).where(
            MediaAsset.clip_id == clip.id, MediaAsset.asset_type == "RENDERED_CLIP"
        )
    )
    if asset is not None:
        return asset
    if not clip.storage_key or clip.render_status != "SUCCEEDED":
        raise PreviewError("CLIP_NOT_AVAILABLE", "a successfully rendered clip is required")
    try:
        size = storage.metadata(clip.storage_key).size_bytes
    except (FileNotFoundError, ValueError) as error:
        raise PreviewError("CLIP_NOT_AVAILABLE", "rendered media is unavailable") from error
    asset = MediaAsset(
        brand_id=clip.brand_id,
        project_id=clip.project_id,
        clip_id=clip.id,
        storage_key=clip.storage_key,
        media_type="video",
        content_type="video/mp4",
        file_size_bytes=size,
        storage_provider="local",
        asset_type="RENDERED_CLIP",
        lifecycle_state="PENDING_REVIEW"
        if clip.approval_status == "PENDING"
        else clip.approval_status,
        retention_deadline=_deadline(settings, "RENDERED_CLIP", clip),
    )
    session.add(asset)
    session.flush()
    return asset


def ensure_source_asset(
    session: Session,
    project: ProductionProject,
    storage: LocalFilesystemStorage,
    settings: Settings | None = None,
) -> MediaAsset | None:
    """Inventory an authorized downloaded source without changing its workflow."""
    settings = settings or get_settings()
    if not project.source_storage_key:
        return None
    asset = session.scalar(
        select(MediaAsset).where(
            MediaAsset.project_id == project.id, MediaAsset.asset_type == "SOURCE_VIDEO"
        )
    )
    if asset is not None:
        return asset
    try:
        size = storage.metadata(project.source_storage_key).size_bytes
    except (FileNotFoundError, ValueError):
        return None
    asset = MediaAsset(
        brand_id=project.brand_id,
        project_id=project.id,
        storage_key=project.source_storage_key,
        media_type="video",
        content_type="video/mp4",
        file_size_bytes=size,
        storage_provider="local",
        asset_type="SOURCE_VIDEO",
        lifecycle_state="ACTIVE",
        retention_deadline=_deadline(settings, "SOURCE_VIDEO"),
    )
    session.add(asset)
    session.flush()
    return asset


def _active_grant(session: Session, clip_id: uuid.UUID, now: datetime) -> PreviewGrant | None:
    return session.scalar(
        select(PreviewGrant)
        .where(
            PreviewGrant.clip_id == clip_id,
            PreviewGrant.revoked_at.is_(None),
            PreviewGrant.expires_at > now,
            (PreviewGrant.maximum_access_count.is_(None))
            | (PreviewGrant.access_count < PreviewGrant.maximum_access_count),
        )
        .order_by(PreviewGrant.created_at.desc())
    )


def issue_preview(
    session: Session,
    actor_id: uuid.UUID | None,
    clip: ProductionClip,
    storage: LocalFilesystemStorage,
    settings: Settings | None = None,
    *,
    refresh: bool = False,
) -> IssuedPreview:
    settings = settings or get_settings()
    if not settings.preview_enabled:
        raise PreviewError("PREVIEW_DISABLED", "private previews are disabled")
    asset = ensure_clip_asset(session, clip, storage, settings)
    # A completed review proxy is preferred, but an unavailable/failed proxy never
    # blocks the authoritative rendered clip from being reviewed.
    proxy = session.scalar(
        select(MediaAsset)
        .where(
            MediaAsset.clip_id == clip.id,
            MediaAsset.asset_type == "PREVIEW_PROXY",
            MediaAsset.lifecycle_state != "DELETED",
        )
        .order_by(MediaAsset.created_at.desc())
    )
    if proxy is not None and storage.exists(proxy.storage_key):
        asset = proxy
    now = _now()
    current = _active_grant(session, clip.id, now)
    if current is not None and not refresh:
        # A raw token cannot be recovered by design.  Callers receive metadata and
        # must deliberately refresh to mint another one-time URL.
        return IssuedPreview(current, "", "", reused=True)
    if current is not None:
        current.revoked_at = now
    raw_token = secrets.token_urlsafe(32)
    grant = PreviewGrant(
        brand_id=clip.brand_id,
        project_id=clip.project_id,
        clip_id=clip.id,
        media_asset_id=asset.id,
        token_hash=_token_digest(raw_token, settings),
        created_by_id=actor_id,
        expires_at=now + timedelta(seconds=settings.preview_token_ttl_seconds),
        maximum_access_count=settings.preview_maximum_access_count,
    )
    session.add(grant)
    session.flush()
    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="preview_grant",
            entity_id=grant.id,
            brand_id=clip.brand_id,
            event_name="preview.grant.refreshed" if refresh else "preview.grant.created",
            payload={"clip_id": str(clip.id), "expires_at": grant.expires_at.isoformat()},
        )
    )
    session.commit()
    base = settings.preview_public_base_url.rstrip("/")
    url = f"{base}/preview/{grant.id}?token={quote(raw_token, safe='')}"
    return IssuedPreview(grant, url, raw_token)


def validate_grant(
    session: Session,
    preview_id: uuid.UUID,
    raw_token: str | None,
    settings: Settings | None = None,
    *,
    count_access: bool = False,
) -> tuple[PreviewGrant, MediaAsset, ProductionClip, ProductionProject]:
    settings = settings or get_settings()
    grant = session.get(PreviewGrant, preview_id)
    if (
        grant is None
        or not raw_token
        or not hmac.compare_digest(grant.token_hash, _token_digest(raw_token, settings))
    ):
        raise PreviewError("INVALID_PREVIEW", "preview is unavailable")
    now = _now()
    if grant.revoked_at is not None or grant.expires_at <= now:
        raise PreviewError("EXPIRED_PREVIEW", "preview is unavailable")
    if grant.maximum_access_count is not None and grant.access_count >= grant.maximum_access_count:
        raise PreviewError("EXHAUSTED_PREVIEW", "preview is unavailable")
    asset = session.get(MediaAsset, grant.media_asset_id)
    clip = session.get(ProductionClip, grant.clip_id)
    project = session.get(ProductionProject, grant.project_id)
    if (
        asset is None
        or clip is None
        or project is None
        or asset.brand_id != grant.brand_id
        or asset.clip_id != clip.id
        or clip.brand_id != grant.brand_id
        or project.id != clip.project_id
        or asset.lifecycle_state == "DELETED"
    ):
        raise PreviewError("UNAVAILABLE_PREVIEW", "preview is unavailable")
    if count_access:
        grant.access_count += 1
        grant.last_accessed_at = now
        asset.last_accessed_at = now
        session.commit()
    return grant, asset, clip, project


def revoke_grants(session: Session, actor_id: uuid.UUID, clip: ProductionClip) -> int:
    now = _now()
    grants = list(
        session.scalars(
            select(PreviewGrant).where(
                PreviewGrant.clip_id == clip.id, PreviewGrant.revoked_at.is_(None)
            )
        )
    )
    for grant in grants:
        grant.revoked_at = now
    if grants:
        session.add(
            AuditEvent(
                actor_id=actor_id,
                entity_type="production_clip",
                entity_id=clip.id,
                brand_id=clip.brand_id,
                event_name="preview.grants.revoked",
                payload={"count": len(grants)},
            )
        )
        session.commit()
    return len(grants)


def parse_range(value: str | None, size: int) -> tuple[int, int] | None:
    if not value:
        return None
    if not value.startswith("bytes=") or "," in value:
        raise PreviewError("INVALID_RANGE", "range is not satisfiable")
    start_text, separator, end_text = value[6:].partition("-")
    if not separator or (not start_text and not end_text):
        raise PreviewError("INVALID_RANGE", "range is not satisfiable")
    try:
        if not start_text:
            length = int(end_text)
            if length <= 0:
                raise ValueError
            return max(0, size - length), size - 1
        start = int(start_text)
        end = int(end_text) if end_text else size - 1
    except ValueError as error:
        raise PreviewError("INVALID_RANGE", "range is not satisfiable") from error
    if start < 0 or end < start or start >= size:
        raise PreviewError("INVALID_RANGE", "range is not satisfiable")
    return start, min(end, size - 1)


def stream_range(handle: object, start: int, end: int, chunk_bytes: int) -> Iterator[bytes]:
    binary = handle
    try:
        binary.seek(start)  # type: ignore[attr-defined]
        remaining = end - start + 1
        while remaining:
            chunk = binary.read(min(chunk_bytes, remaining))  # type: ignore[attr-defined]
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        binary.close()  # type: ignore[attr-defined]


def _publish_protects(session: Session, clip_id: uuid.UUID) -> bool:
    states = {
        PublishRequestStatus.AWAITING_CONFIRMATION,
        PublishRequestStatus.SCHEDULED,
        PublishRequestStatus.QUEUED,
        PublishRequestStatus.UPLOADING,
    }
    return (
        session.scalar(
            select(PublishRequest.id)
            .where(PublishRequest.clip_id == clip_id, PublishRequest.status.in_(states))
            .limit(1)
        )
        is not None
    )


def retention_reason(
    session: Session, asset: MediaAsset, now: datetime | None = None
) -> str | None:
    now = now or _now()
    if asset.lifecycle_state == "DELETED" or asset.administrative_hold:
        return None
    if asset.retention_deadline is None or asset.retention_deadline > now:
        return None
    active = session.scalar(
        select(PreviewGrant.id)
        .where(
            PreviewGrant.media_asset_id == asset.id,
            PreviewGrant.revoked_at.is_(None),
            PreviewGrant.expires_at > now,
        )
        .limit(1)
    )
    if active is not None:
        return None
    if asset.asset_type == "PREVIEW_PROXY":
        return "preview_proxy_expired"
    clip = session.get(ProductionClip, asset.clip_id) if asset.clip_id else None
    if clip is not None:
        if _publish_protects(session, clip.id):
            return None
        if clip.approval_status == "REJECTED":
            return "rejected_clip_expired"
        published = session.scalar(
            select(PublishRequest.id)
            .where(
                PublishRequest.clip_id == clip.id,
                PublishRequest.status == PublishRequestStatus.SUCCEEDED,
            )
            .limit(1)
        )
        if published is not None:
            return "published_clip_retention_elapsed"
        return None  # an unresolved review or failed/reconcilable workflow is retained
    if asset.asset_type in {"TEMPORARY_PROCESSING_FILE", "TRANSCRIPTION_TEMP_FILE"}:
        return "temporary_media_expired"
    if asset.asset_type == "SOURCE_VIDEO":
        project = session.get(ProductionProject, asset.project_id) if asset.project_id else None
        clips = (
            list(
                session.scalars(
                    select(ProductionClip).where(ProductionClip.project_id == project.id)
                )
            )
            if project
            else []
        )
        if clips and all(item.render_status == "SUCCEEDED" for item in clips):
            return "source_retention_elapsed"
    return None


def cleanup_expired_media(
    session: Session,
    storage: LocalFilesystemStorage,
    settings: Settings | None = None,
    *,
    dry_run: bool | None = None,
) -> CleanupResult:
    settings = settings or get_settings()
    if not _cleanup_lock.acquire(blocking=False):
        return CleanupResult(0, 0, 0, 0, bool(dry_run))
    try:
        actual_dry_run = settings.cleanup_dry_run if dry_run is None else dry_run
        assets = list(
            session.scalars(
                select(MediaAsset)
                .where(MediaAsset.lifecycle_state != "DELETED")
                .order_by(MediaAsset.retention_deadline)
                .limit(settings.cleanup_batch_size)
            )
        )
        selected = deleted = reclaimed = failures = 0
        for asset in assets:
            reason = retention_reason(session, asset)
            if not reason:
                continue
            selected += 1
            if actual_dry_run:
                continue
            try:
                size = asset.file_size_bytes
                # LocalFilesystemStorage validates provider-relative keys before it
                # reaches the filesystem; a missing object is an idempotent delete.
                if storage.exists(asset.storage_key):
                    storage.delete(asset.storage_key)
                asset.lifecycle_state = "DELETED"
                asset.deleted_at = _now()
                asset.deletion_reason = reason
                asset.former_size_bytes = size
                asset.file_size_bytes = 0
                asset.deletion_error = None
                deleted += 1
                reclaimed += size
                session.add(
                    AuditEvent(
                        entity_type="media_asset",
                        entity_id=asset.id,
                        brand_id=asset.brand_id,
                        event_name="media.retention.deleted",
                        payload={"reason": reason, "bytes": size},
                    )
                )
                session.commit()
            except (OSError, ValueError):
                asset.deletion_attempts += 1
                asset.deletion_error = "storage deletion failed"
                session.commit()
                failures += 1
        return CleanupResult(selected, deleted, reclaimed, failures, actual_dry_run)
    finally:
        _cleanup_lock.release()


def extend_retention(
    session: Session, actor_id: uuid.UUID, asset: MediaAsset, seconds: int
) -> MediaAsset:
    asset.retention_deadline = _now() + timedelta(seconds=seconds)
    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="media_asset",
            entity_id=asset.id,
            brand_id=asset.brand_id,
            event_name="media.retention.extended",
            payload={"seconds": seconds},
        )
    )
    session.commit()
    return asset


def set_hold(session: Session, actor_id: uuid.UUID, asset: MediaAsset, enabled: bool) -> MediaAsset:
    asset.administrative_hold = enabled
    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="media_asset",
            entity_id=asset.id,
            brand_id=asset.brand_id,
            event_name="media.hold.placed" if enabled else "media.hold.removed",
        )
    )
    session.commit()
    return asset


def storage_summary(session: Session, storage: LocalFilesystemStorage) -> dict[str, object]:
    usage = shutil.disk_usage(storage.root)
    rows = session.execute(
        select(MediaAsset.asset_type, func.coalesce(func.sum(MediaAsset.file_size_bytes), 0))
        .where(MediaAsset.lifecycle_state != "DELETED")
        .group_by(MediaAsset.asset_type)
    ).all()
    by_type = {str(kind): int(total) for kind, total in rows}
    reclaimable = sum(
        asset.file_size_bytes
        for asset in session.scalars(
            select(MediaAsset).where(MediaAsset.lifecycle_state != "DELETED")
        )
        if retention_reason(session, asset)
    )
    oldest = session.scalar(
        select(MediaAsset.created_at)
        .where(MediaAsset.lifecycle_state == "PENDING_REVIEW")
        .order_by(MediaAsset.created_at)
        .limit(1)
    )
    return {
        "provider": "local",
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "used_percent": round((usage.used / usage.total) * 100, 2) if usage.total else 0,
        "source_bytes": by_type.get("SOURCE_VIDEO", 0),
        "rendered_clip_bytes": by_type.get("RENDERED_CLIP", 0),
        "proxy_bytes": by_type.get("PREVIEW_PROXY", 0),
        "temporary_bytes": sum(
            value
            for kind, value in by_type.items()
            if kind in {"TEMPORARY_PROCESSING_FILE", "TRANSCRIPTION_TEMP_FILE"}
        ),
        "reclaimable_bytes": reclaimable,
        "oldest_pending_review_at": oldest,
    }


def generate_proxy(
    session: Session,
    actor_id: uuid.UUID,
    clip: ProductionClip,
    storage: LocalFilesystemStorage,
    settings: Settings | None = None,
) -> MediaAsset | None:
    """Generate a disposable review proxy; errors are audited and never affect the clip."""
    settings = settings or get_settings()
    if not settings.preview_proxy_enabled:
        return None
    source = ensure_clip_asset(session, clip, storage, settings)
    profile = {
        "width": settings.preview_proxy_width,
        "height": settings.preview_proxy_height,
        "video": settings.preview_proxy_video_bitrate,
        "audio": settings.preview_proxy_audio_bitrate,
        "fps": settings.preview_proxy_fps,
    }
    existing = session.scalar(
        select(MediaAsset)
        .where(
            MediaAsset.clip_id == clip.id,
            MediaAsset.asset_type == "PREVIEW_PROXY",
            MediaAsset.lifecycle_state != "DELETED",
        )
        .order_by(MediaAsset.created_at.desc())
    )
    if (
        existing is not None
        and existing.storage_metadata
        and existing.storage_metadata.get("proxy_profile") == profile
    ):
        return existing
    work = Path(settings.video_work_root) / "preview-proxies" / str(clip.id)
    work.mkdir(parents=True, exist_ok=True)
    input_path, output_path = work / "input.mp4", work / "review.mp4"
    try:
        with storage.open(source.storage_key) as incoming, input_path.open("wb") as output:
            shutil.copyfileobj(incoming, output)
        command = [
            settings.ffmpeg_path,
            "-y",
            "-i",
            str(input_path),
            "-vf",
            f"scale='min({settings.preview_proxy_width},iw)':min'({settings.preview_proxy_height},ih)':force_original_aspect_ratio=decrease",
            "-r",
            str(settings.preview_proxy_fps),
            "-c:v",
            "libx264",
            "-b:v",
            settings.preview_proxy_video_bitrate,
            "-c:a",
            "aac",
            "-b:a",
            settings.preview_proxy_audio_bitrate,
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=settings.preview_proxy_timeout_seconds,
            check=True,
        )
        if output_path.stat().st_size > settings.preview_proxy_max_bytes:
            raise OSError("proxy exceeds configured size")
        temporary = storage.create_temporary()
        with output_path.open("rb") as proxy:
            while data := proxy.read(262_144):
                storage.write_chunk(temporary, data)
        stored = storage.finalize(temporary, ".mp4")
        asset = MediaAsset(
            brand_id=clip.brand_id,
            project_id=clip.project_id,
            clip_id=clip.id,
            storage_key=stored.key,
            media_type="video",
            content_type="video/mp4",
            file_size_bytes=stored.size_bytes,
            storage_provider="local",
            asset_type="PREVIEW_PROXY",
            lifecycle_state="ACTIVE",
            retention_deadline=_deadline(settings, "PREVIEW_PROXY"),
            storage_metadata={"proxy_profile": profile},
        )
        session.add(asset)
        session.add(
            AuditEvent(
                actor_id=actor_id,
                entity_type="production_clip",
                entity_id=clip.id,
                brand_id=clip.brand_id,
                event_name="preview.proxy.generated",
                payload={"bytes": stored.size_bytes},
            )
        )
        session.commit()
        return asset
    except (OSError, subprocess.SubprocessError):
        session.add(
            AuditEvent(
                actor_id=actor_id,
                entity_type="production_clip",
                entity_id=clip.id,
                brand_id=clip.brand_id,
                event_name="preview.proxy.failed",
            )
        )
        session.commit()
        return None
    finally:
        shutil.rmtree(work, ignore_errors=True)
