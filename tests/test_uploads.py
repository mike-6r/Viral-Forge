import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select

from app.common.config import Settings
from app.content.models import ContentItem, ContentSource, ContentStatus, MediaAsset, Platform
from app.ingestion.models import DuplicateMatch, IngestionStatus
from app.ingestion.storage import LocalFilesystemStorage
from app.ingestion.upload import (
    DetectedMediaType,
    UploadError,
    UploadErrorCategory,
    clean_filename,
    detect_media_type,
    submit_upload,
)
from app.sources.models import Source, SourcePolicy
from tests.conftest import DEV_ACTOR_ID


def mp4_bytes(payload: bytes = b"video") -> bytes:
    return b"\x00\x00\x00\x18ftypisom" + payload


async def stream(*chunks: bytes) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


def upload_settings() -> Settings:
    return Settings(upload_max_bytes=2_048, upload_chunk_bytes=1_024)


@pytest.mark.parametrize(
    ("data", "media_type"),
    [
        (mp4_bytes(), DetectedMediaType.MP4),
        (b"\x00\x00\x00\x18ftypqt  movie", DetectedMediaType.MOV),
        (b"\x1a\x45\xdf\xa3webm payload", DetectedMediaType.WEBM),
        (b"\x1a\x45\xdf\xa3matroska payload", DetectedMediaType.MKV),
    ],
)
def test_detects_supported_container_signatures(data: bytes, media_type: DetectedMediaType):
    assert detect_media_type(data)[0] is media_type


@pytest.mark.parametrize(
    "name", ["../../video.mp4", "..\\..\\video.mp4", "C:\\video.mp4", "NUL.mp4", "bad\x00.mp4"]
)
def test_rejects_unsafe_filenames(name: str):
    with pytest.raises(UploadError) as error:
        clean_filename(name, 255)
    assert error.value.category is UploadErrorCategory.UNSAFE_FILENAME


def test_local_storage_finalizes_atomically_and_rejects_escape(tmp_path):  # type: ignore[no-untyped-def]
    storage = LocalFilesystemStorage(tmp_path / "outside-source-tree")
    temporary = storage.create_temporary()
    storage.write_chunk(temporary, b"abc")
    stored = storage.finalize(temporary, ".mp4")
    assert stored.key.startswith("assets/")
    assert storage.metadata(stored.key).size_bytes == 3
    with storage.open(stored.key) as handle:
        assert handle.read() == b"abc"
    assert not storage.exists("../escape")
    storage.delete(stored.key)
    assert not storage.exists(stored.key)


def test_successful_upload_records_asset_provenance_lifecycle_and_hash(session, tmp_path):  # type: ignore[no-untyped-def]
    data = mp4_bytes(b"chunked")
    job = asyncio.run(
        submit_upload(
            session,
            DEV_ACTOR_ID,
            stream(data[:8], data[8:]),
            "video.mp4",
            "video/mp4",
            str(uuid.uuid4()),
            notes="claimed owner",
            rights_declaration="OWNER_SUBMITTED",
            storage=LocalFilesystemStorage(tmp_path),
            settings=upload_settings(),
        )
    )
    asset = session.get(MediaAsset, job.result_asset_id)
    content = session.get(ContentItem, job.result_content_id)
    assert job.status is IngestionStatus.SUCCEEDED
    assert asset is not None and asset.checksum == hashlib.sha256(data).hexdigest()
    assert asset.asset_status == "VERIFICATION_REQUIRED"
    assert content is not None and content.status is ContentStatus.SOURCE_VERIFICATION_REQUIRED
    assert (
        session.scalar(
            select(ContentSource.content_id).where(ContentSource.content_id == content.id)
        )
        == content.id
    )


def test_duplicate_upload_reuses_physical_asset_and_keeps_new_provenance(session, tmp_path):  # type: ignore[no-untyped-def]
    data = mp4_bytes(b"same")
    storage = LocalFilesystemStorage(tmp_path)
    first = asyncio.run(
        submit_upload(
            session,
            DEV_ACTOR_ID,
            stream(data),
            "first.mp4",
            "video/mp4",
            str(uuid.uuid4()),
            storage=storage,
            settings=upload_settings(),
        )
    )
    second = asyncio.run(
        submit_upload(
            session,
            DEV_ACTOR_ID,
            stream(data),
            "second.mp4",
            "video/mp4",
            str(uuid.uuid4()),
            storage=storage,
            settings=upload_settings(),
        )
    )
    assert first.result_asset_id == second.result_asset_id
    assert len(list(session.scalars(select(MediaAsset)))) == 1
    assert len(list(session.scalars(select(ContentSource)))) == 2
    assert session.scalar(select(DuplicateMatch.id)) is not None


def test_upload_failure_cleans_temporary_file_and_persists_failed_job(session, tmp_path):  # type: ignore[no-untyped-def]
    storage = LocalFilesystemStorage(tmp_path)
    job = asyncio.run(
        submit_upload(
            session,
            DEV_ACTOR_ID,
            stream(b"not media"),
            "fake.mp4",
            "video/mp4",
            str(uuid.uuid4()),
            storage=storage,
            settings=upload_settings(),
        )
    )
    assert job.status is IngestionStatus.FAILED
    assert job.error_category == UploadErrorCategory.INVALID_FILE_SIGNATURE.value
    assert list(storage.tmp_root.iterdir()) == []
    assert session.scalar(select(ContentItem.id)) is None


def test_upload_size_and_source_policy_are_enforced(session, tmp_path):  # type: ignore[no-untyped-def]
    storage = LocalFilesystemStorage(tmp_path)
    big = mp4_bytes(b"x" * 2_100)
    too_large = asyncio.run(
        submit_upload(
            session,
            DEV_ACTOR_ID,
            stream(big[:1_024], big[1_024:2_048], big[2_048:]),
            "big.mp4",
            "video/mp4",
            str(uuid.uuid4()),
            storage=storage,
            settings=upload_settings(),
        )
    )
    assert too_large.error_category == UploadErrorCategory.FILE_TOO_LARGE.value

    source = Source(platform=Platform.MANUAL, normalized_url="upload://policy")
    session.add(source)
    session.flush()
    session.add(
        SourcePolicy(
            source_id=source.id, policy_version="test", permitted_media_types=["video/webm"]
        )
    )
    session.commit()
    rejected = asyncio.run(
        submit_upload(
            session,
            DEV_ACTOR_ID,
            stream(mp4_bytes()),
            "policy.mp4",
            "video/mp4",
            str(uuid.uuid4()),
            source_id=source.id,
            storage=storage,
            settings=upload_settings(),
        )
    )
    assert rejected.error_category == UploadErrorCategory.POLICY_VIOLATION.value


def test_upload_idempotency_replay_and_conflict(session, tmp_path):  # type: ignore[no-untyped-def]
    key = str(uuid.uuid4())
    storage = LocalFilesystemStorage(tmp_path)
    first = asyncio.run(
        submit_upload(
            session,
            DEV_ACTOR_ID,
            stream(mp4_bytes()),
            "same.mp4",
            "video/mp4",
            key,
            storage=storage,
            settings=upload_settings(),
        )
    )
    replay = asyncio.run(
        submit_upload(
            session,
            DEV_ACTOR_ID,
            stream(mp4_bytes(b"other")),
            "same.mp4",
            "video/mp4",
            key,
            storage=storage,
            settings=upload_settings(),
        )
    )
    assert first.id == replay.id
    with pytest.raises(UploadError) as error:
        asyncio.run(
            submit_upload(
                session,
                DEV_ACTOR_ID,
                stream(mp4_bytes()),
                "different.mp4",
                "video/mp4",
                key,
                storage=storage,
                settings=upload_settings(),
            )
        )
    assert error.value.category is UploadErrorCategory.IDEMPOTENCY_CONFLICT
