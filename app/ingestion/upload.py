"""Manual media-upload workflow; this module owns stream, storage, and persistence coordination."""

import hashlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.common.config import Settings, get_settings
from app.common.errors import DomainError, PreconditionError
from app.content.lifecycle import transition
from app.content.models import ContentItem, ContentSource, ContentStatus, MediaAsset, Platform
from app.ingestion.models import (
    DuplicateMatch,
    DuplicateOutcome,
    IngestionJob,
    IngestionMethod,
    IngestionStatus,
)
from app.ingestion.policy import enforce_upload_policy
from app.ingestion.storage import LocalFilesystemStorage, Storage
from app.sources.models import Source, SourcePolicy, SourceStatus, SourceType


class UploadErrorCategory(StrEnum):
    INVALID_UPLOAD = "INVALID_UPLOAD"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    EMPTY_FILE = "EMPTY_FILE"
    UNSUPPORTED_CONTENT_TYPE = "UNSUPPORTED_CONTENT_TYPE"
    MIME_MISMATCH = "MIME_MISMATCH"
    INVALID_FILE_SIGNATURE = "INVALID_FILE_SIGNATURE"
    UNSAFE_FILENAME = "UNSAFE_FILENAME"
    SOURCE_NOT_ALLOWED = "SOURCE_NOT_ALLOWED"
    SOURCE_INACTIVE = "SOURCE_INACTIVE"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    STORAGE_FAILURE = "STORAGE_FAILURE"
    PARTIAL_UPLOAD_FAILURE = "PARTIAL_UPLOAD_FAILURE"
    DATABASE_FAILURE = "DATABASE_FAILURE"


class UploadError(DomainError):
    def __init__(self, category: UploadErrorCategory, message: str) -> None:
        super().__init__(message)
        self.category = category


class DetectedMediaType(StrEnum):
    MP4 = "video/mp4"
    MOV = "video/quicktime"
    WEBM = "video/webm"
    MKV = "video/x-matroska"


def clean_filename(filename: str | None, maximum: int) -> str:
    if not filename:
        raise UploadError(UploadErrorCategory.UNSAFE_FILENAME, "an upload filename is required")
    value = filename.strip()
    forbidden = ("\x00", "\r", "\n", "/", "\\", ":", "\u2215", "\u2044", "\uff0f", "\uff3c")
    if len(value) > maximum or any(character in value for character in forbidden):
        raise UploadError(UploadErrorCategory.UNSAFE_FILENAME, "upload filename is unsafe")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise UploadError(UploadErrorCategory.UNSAFE_FILENAME, "upload filename is unsafe")
    stem = value.split(".", 1)[0].upper()
    if stem in {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "LPT1", "LPT2", "LPT3"}:
        raise UploadError(UploadErrorCategory.UNSAFE_FILENAME, "upload filename is reserved")
    return value


def detect_media_type(prefix: bytes) -> tuple[DetectedMediaType, str]:
    if len(prefix) < 12:
        raise UploadError(
            UploadErrorCategory.INVALID_FILE_SIGNATURE, "file signature is incomplete"
        )
    if prefix[4:8] == b"ftyp":
        brand = prefix[8:12]
        if brand == b"qt  ":
            return DetectedMediaType.MOV, "mov"
        return DetectedMediaType.MP4, "mp4"
    if prefix.startswith(b"\x1a\x45\xdf\xa3"):
        if b"webm" in prefix[:4096].lower():
            return DetectedMediaType.WEBM, "webm"
        return DetectedMediaType.MKV, "mkv"
    raise UploadError(
        UploadErrorCategory.UNSUPPORTED_CONTENT_TYPE,
        "uploaded file is not a supported video container",
    )


def _suffix(media_type: DetectedMediaType) -> str:
    return {
        DetectedMediaType.MP4: ".mp4",
        DetectedMediaType.MOV: ".mov",
        DetectedMediaType.WEBM: ".webm",
        DetectedMediaType.MKV: ".mkv",
    }[media_type]


def _latest_policy(session: Session, source: Source) -> SourcePolicy | None:
    return session.scalar(
        select(SourcePolicy)
        .where(SourcePolicy.source_id == source.id)
        .order_by(SourcePolicy.created_at.desc())
    )


def _upload_metadata(
    filename: str,
    declared_media_type: str | None,
    notes: str | None,
    source_url: str | None,
    rights_declaration: str | None,
    rights_notes: str | None,
    attribution: str | None,
    correlation_id: str | None,
) -> dict[str, object]:
    return {
        "manual_upload": {
            "original_filename": filename,
            "declared_media_type": declared_media_type,
            "submission_notes": notes,
            "original_source_url": source_url,
            "rights_declaration": rights_declaration,
            "rights_notes": rights_notes,
            "attribution": attribution,
            "rights_evidence_supplied": False,
            "correlation_id": correlation_id,
        }
    }


def _apply_upload_policy(
    source: Source,
    policy: SourcePolicy | None,
    media_type: str | None = None,
    size: int | None = None,
) -> None:
    try:
        enforce_upload_policy(source, policy, media_type, size)
    except PreconditionError as error:
        raise UploadError(
            UploadErrorCategory.POLICY_VIOLATION, "source policy rejected the upload"
        ) from error


def _fail_job(
    session: Session,
    job_id: uuid.UUID,
    actor_id: uuid.UUID,
    correlation_id: str | None,
    error: UploadError,
) -> IngestionJob:
    session.rollback()
    job = session.get(IngestionJob, job_id)
    if job is None:
        raise RuntimeError("persisted upload job was unavailable")
    job.status = IngestionStatus.FAILED
    job.error_category = error.category.value
    job.error_message = str(error)
    job.completed_at = datetime.now(UTC)
    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="ingestion_job",
            entity_id=job.id,
            event_name="ingestion.upload.failed",
            correlation_id=correlation_id,
            payload={"category": error.category.value},
        )
    )
    session.commit()
    return job


async def submit_upload(
    session: Session,
    actor_id: uuid.UUID,
    chunks: AsyncIterator[bytes],
    filename: str | None,
    declared_media_type: str | None,
    idempotency_key: str,
    source_id: uuid.UUID | None = None,
    notes: str | None = None,
    original_source_url: str | None = None,
    rights_declaration: str | None = None,
    rights_notes: str | None = None,
    attribution: str | None = None,
    correlation_id: str | None = None,
    storage: Storage | None = None,
    settings: Settings | None = None,
) -> IngestionJob:
    settings = settings or get_settings()
    display_filename = clean_filename(filename, settings.upload_filename_max_length)
    if notes is not None and len(notes) > settings.upload_notes_max_length:
        raise UploadError(
            UploadErrorCategory.INVALID_UPLOAD, "submission notes exceed the configured limit"
        )
    existing = session.scalar(
        select(IngestionJob).where(IngestionJob.idempotency_key == idempotency_key)
    )
    if existing is not None:
        existing_source = (
            session.get(Source, existing.source_id) if existing.source_id is not None else None
        )
        raw_metadata = existing_source.provider_metadata or {} if existing_source else {}
        candidate_metadata = raw_metadata.get("manual_upload", {})
        upload_metadata = candidate_metadata if isinstance(candidate_metadata, dict) else {}
        existing_name = upload_metadata.get("original_filename")
        if existing_name is not None and existing_name != display_filename:
            raise UploadError(
                UploadErrorCategory.IDEMPOTENCY_CONFLICT,
                "idempotency key conflicts with upload metadata",
            )
        return existing
    source = session.get(Source, source_id) if source_id is not None else None
    if source_id is not None and source is None:
        raise UploadError(UploadErrorCategory.SOURCE_NOT_ALLOWED, "source was not found")
    if source is None:
        source = Source(
            platform=Platform.MANUAL,
            normalized_url=f"upload://{uuid.uuid4()}",
            source_type=SourceType.MANUAL_UPLOAD,
            status=SourceStatus.PENDING_REVIEW,
        )
        session.add(source)
        session.flush()
    job = IngestionJob(
        method=IngestionMethod.MANUAL_UPLOAD,
        status=IngestionStatus.RUNNING,
        actor_id=actor_id,
        source_id=source.id,
        requested_url=f"upload:{display_filename}",
        idempotency_key=idempotency_key,
        attempts=1,
        started_at=datetime.now(UTC),
        heartbeat_at=datetime.now(UTC),
        correlation_id=correlation_id,
    )
    session.add(job)
    session.flush()
    job_id = job.id
    session.commit()
    storage = storage or LocalFilesystemStorage(Path(settings.local_storage_root))
    temporary_key: str | None = None
    finalized_key: str | None = None
    try:
        source = session.get(Source, source.id) or source
        policy = _latest_policy(session, source)
        _apply_upload_policy(source, policy)
        temporary_key = storage.create_temporary()
        session.add(
            AuditEvent(
                actor_id=actor_id,
                entity_type="ingestion_job",
                entity_id=job_id,
                event_name="storage.temp.created",
                correlation_id=correlation_id,
            )
        )
        digest = hashlib.sha256()
        received = 0
        async for chunk in chunks:
            if not chunk:
                continue
            if len(chunk) > settings.upload_chunk_bytes:
                raise UploadError(
                    UploadErrorCategory.INVALID_UPLOAD, "upload chunk exceeds the configured limit"
                )
            received += len(chunk)
            source_limit = (
                policy.max_file_size_bytes if policy is not None else settings.upload_max_bytes
            )
            if received > min(settings.upload_max_bytes, source_limit):
                raise UploadError(
                    UploadErrorCategory.FILE_TOO_LARGE, "upload exceeds the configured size limit"
                )
            storage.write_chunk(temporary_key, chunk)
            digest.update(chunk)
            job.progress = min(99, int(received * 100 / max(1, settings.upload_max_bytes)))
            job.heartbeat_at = datetime.now(UTC)
        if received == 0:
            raise UploadError(UploadErrorCategory.EMPTY_FILE, "uploaded file is empty")
        media_type, container = detect_media_type(storage.read_prefix(temporary_key, 4096))
        if declared_media_type and declared_media_type.lower() != media_type.value:
            raise UploadError(
                UploadErrorCategory.MIME_MISMATCH,
                "declared media type does not match the file signature",
            )
        _apply_upload_policy(source, policy, media_type.value, received)
        checksum = digest.hexdigest()
        duplicate_asset = session.scalar(select(MediaAsset).where(MediaAsset.checksum == checksum))
        source.provider_metadata = _upload_metadata(
            display_filename,
            declared_media_type,
            notes,
            original_source_url,
            rights_declaration,
            rights_notes,
            attribution,
            correlation_id,
        )
        if duplicate_asset is not None:
            storage.delete(temporary_key)
            temporary_key = None
            existing_provenance = session.scalar(
                select(ContentSource.id).where(
                    ContentSource.content_id == duplicate_asset.content_id,
                    ContentSource.source_id == source.id,
                )
            )
            if existing_provenance is None:
                session.add(
                    ContentSource(
                        content_id=duplicate_asset.content_id,
                        source_id=source.id,
                        source_url=f"upload://{job_id}",
                    )
                )
            session.add(
                DuplicateMatch(
                    content_id=duplicate_asset.content_id,
                    matched_content_id=duplicate_asset.content_id,
                    outcome=DuplicateOutcome.FILE_HASH_DUPLICATE,
                    evidence=checksum,
                )
            )
            job.status = IngestionStatus.SUCCEEDED
            job.result_content_id = duplicate_asset.content_id
            job.result_asset_id = duplicate_asset.id
            job.progress = 100
            job.completed_at = datetime.now(UTC)
            session.add(
                AuditEvent(
                    actor_id=actor_id,
                    entity_type="ingestion_job",
                    entity_id=job_id,
                    event_name="ingestion.upload.duplicate",
                    correlation_id=correlation_id,
                    payload={
                        "outcome": DuplicateOutcome.FILE_HASH_DUPLICATE.value,
                        "asset_id": str(duplicate_asset.id),
                        "file_size": received,
                    },
                )
            )
            session.commit()
            return job
        finalized = storage.finalize(temporary_key, _suffix(media_type))
        finalized_key = finalized.key
        temporary_key = None
        item = ContentItem(
            title=display_filename, status=ContentStatus.DISCOVERED, source_provenance_complete=True
        )
        session.add(item)
        session.flush()
        asset = MediaAsset(
            content_id=item.id,
            storage_key=finalized.key,
            media_type=media_type.value,
            checksum=checksum,
            storage_provider="local",
            original_filename=display_filename,
            display_filename=display_filename,
            detected_media_type=media_type.value,
            declared_media_type=declared_media_type,
            container_type=container,
            file_size_bytes=received,
            uploader_id=actor_id,
            source_id=source.id,
            asset_status="VERIFICATION_REQUIRED",
            correlation_id=correlation_id,
            storage_metadata={"provider": "local", "key": finalized.key},
        )
        session.add(asset)
        session.flush()
        session.add(
            ContentSource(content_id=item.id, source_id=source.id, source_url=f"upload://{job_id}")
        )
        transition(
            session, item, ContentStatus.IMPORTED, actor_id, "media upload stored", correlation_id
        )
        transition(
            session,
            item,
            ContentStatus.SOURCE_VERIFICATION_REQUIRED,
            actor_id,
            "manual upload requires verification",
            correlation_id,
        )
        job.status = IngestionStatus.SUCCEEDED
        job.result_content_id = item.id
        job.result_asset_id = asset.id
        job.progress = 100
        job.completed_at = datetime.now(UTC)
        session.add_all(
            [
                AuditEvent(
                    actor_id=actor_id,
                    entity_type="ingestion_job",
                    entity_id=job_id,
                    event_name="ingestion.upload.stored",
                    correlation_id=correlation_id,
                    payload={
                        "asset_id": str(asset.id),
                        "file_size": received,
                        "media_type": media_type.value,
                    },
                ),
                AuditEvent(
                    actor_id=actor_id,
                    entity_type="media_asset",
                    entity_id=asset.id,
                    event_name="storage.asset.finalized",
                    correlation_id=correlation_id,
                    payload={"file_size": received, "media_type": media_type.value},
                ),
            ]
        )
        session.commit()
        return job
    except UploadError as error:
        if temporary_key is not None:
            storage.delete(temporary_key)
        if finalized_key is not None:
            storage.delete(finalized_key)
        return _fail_job(session, job_id, actor_id, correlation_id, error)
    except (OSError, ValueError):
        if temporary_key is not None:
            storage.delete(temporary_key)
        if finalized_key is not None:
            storage.delete(finalized_key)
        return _fail_job(
            session,
            job_id,
            actor_id,
            correlation_id,
            UploadError(UploadErrorCategory.STORAGE_FAILURE, "upload storage failed"),
        )
