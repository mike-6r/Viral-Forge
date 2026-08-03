from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.content.models import MediaAsset
from app.ingestion.storage import LocalFilesystemStorage
from app.manual_publishing.models import ManualAnalyticsCheckpoint, ManualPublication
from app.manual_publishing.service import (
    ManualPublicationError,
    add_manual_metrics,
    record_manual_publication,
    update_checkpoint,
)
from app.media_preview.models import ClipDownloadGrant
from app.media_preview.service import (
    PreviewError,
    issue_full_quality_download,
    retention_reason,
    validate_download_grant,
)
from app.publishing.models import PublishRequest
from tests.conftest import DEV_ACTOR_ID
from tests.test_publishing_foundation import _ready_publish_context


def _rendered_asset(session, clip, tmp_path):  # type: ignore[no-untyped-def]
    storage = LocalFilesystemStorage(tmp_path / "storage")
    temporary = storage.create_temporary()
    storage.write_chunk(temporary, b"full-quality-video")
    stored = storage.finalize(temporary, ".mp4")
    clip.storage_key = stored.key
    asset = MediaAsset(
        brand_id=clip.brand_id,
        project_id=clip.project_id,
        clip_id=clip.id,
        storage_key=stored.key,
        media_type="video",
        content_type="video/mp4",
        file_size_bytes=stored.size_bytes,
        storage_provider="local",
        asset_type="RENDERED_CLIP",
        lifecycle_state="APPROVED",
        retention_deadline=datetime.now(UTC) - timedelta(seconds=1),
    )
    session.add(asset)
    session.commit()
    return asset, storage


def test_full_quality_download_is_hashed_limited_and_holds_retention(session, tmp_path):  # type: ignore[no-untyped-def]
    _, clip, package, _ = _ready_publish_context(session)
    asset, storage = _rendered_asset(session, clip, tmp_path)
    issued = issue_full_quality_download(session, DEV_ACTOR_ID, clip, storage, package)
    grant = session.get(ClipDownloadGrant, issued.grant.id)
    assert grant is not None
    assert issued.raw_token not in grant.token_hash
    assert issued.raw_token not in issued.url.split("#", 1)[0]
    assert retention_reason(session, asset) is None
    _, found, _, _ = validate_download_grant(
        session, grant.id, issued.raw_token, count_access=True
    )
    assert found.id == asset.id
    assert grant.access_count == 1
    with pytest.raises(PreviewError):
        validate_download_grant(session, grant.id, "wrong-token")


def test_manual_recording_creates_checkpoints_and_never_creates_provider_work(session, tmp_path):  # type: ignore[no-untyped-def]
    _, clip, package, _ = _ready_publish_context(session)
    asset, _ = _rendered_asset(session, clip, tmp_path)
    publication = record_manual_publication(
        session,
        DEV_ACTOR_ID,
        clip,
        package,
        asset,
        platform="TIKTOK",
        destination_label="BodycamsDailyHQ",
        public_post_url="https://www.tiktok.com/@bodycamsdailyhq/video/123456789",
        published_at=datetime.now(UTC),
        notes="Recorded after phone upload.",
    )
    assert session.scalar(select(ManualPublication).where(ManualPublication.id == publication.id))
    assert len(list(session.scalars(select(ManualAnalyticsCheckpoint)))) == 5
    assert session.scalar(select(PublishRequest)) is None
    with pytest.raises(ManualPublicationError, match="already recorded"):
        record_manual_publication(
            session,
            DEV_ACTOR_ID,
            clip,
            package,
            asset,
            platform="TIKTOK",
            destination_label="BodycamsDailyHQ",
            public_post_url="https://www.tiktok.com/@bodycamsdailyhq/video/123456789",
            published_at=datetime.now(UTC),
        )
    snapshot = add_manual_metrics(
        session,
        DEV_ACTOR_ID,
        publication,
        {"views": 120, "likes": 10, "profile_visits": 3, "completion_rate": 54.5},
        datetime.now(UTC) + timedelta(hours=1),
    )
    assert snapshot.manual_publication_id == publication.id
    checkpoint = session.scalar(
        select(ManualAnalyticsCheckpoint).where(
            ManualAnalyticsCheckpoint.manual_publication_id == publication.id,
            ManualAnalyticsCheckpoint.checkpoint_key == "6H",
        )
    )
    assert checkpoint is not None
    update_checkpoint(session, DEV_ACTOR_ID, checkpoint, "SNOOZE", snooze_hours=2)
    assert checkpoint.snoozed_until is not None
