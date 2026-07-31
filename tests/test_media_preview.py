import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.brands.models import Brand, Workspace
from app.common.config import Settings
from app.content.models import MediaAsset
from app.ingestion.storage import LocalFilesystemStorage
from app.media_preview.models import PreviewGrant
from app.media_preview.service import (
    PreviewError,
    cleanup_expired_media,
    issue_preview,
    parse_range,
    stream_range,
    validate_grant,
)
from app.production.models import ProductionClip, ProductionProject


def prepared_clip(session, tmp_path):  # type: ignore[no-untyped-def]
    workspace = Workspace(name="Preview", slug=f"preview-{uuid.uuid4().hex[:8]}")
    session.add(workspace)
    session.flush()
    brand = Brand(workspace_id=workspace.id, name="Preview", slug=f"preview-{uuid.uuid4().hex[:8]}")
    session.add(brand)
    project = ProductionProject(brand_id=brand.id, source_url=f"https://example.test/{uuid.uuid4()}", created_actor_id=uuid.UUID("a1111111-1111-1111-1111-111111111111"), status="CLIPS_READY")
    session.add(project)
    session.flush()
    storage = LocalFilesystemStorage(tmp_path / "storage")
    temporary = storage.create_temporary()
    storage.write_chunk(temporary, b"0123456789")
    stored = storage.finalize(temporary, ".mp4")
    clip = ProductionClip(project_id=project.id, brand_id=brand.id, clip_number=1, start_seconds=0, end_seconds=10, duration_seconds=10, storage_key=stored.key, render_status="SUCCEEDED")
    session.add(clip)
    session.commit()
    return clip, storage


def test_preview_hashes_token_and_streams_ranges(session, tmp_path):  # type: ignore[no-untyped-def]
    clip, storage = prepared_clip(session, tmp_path)
    settings = Settings(local_storage_root=str(tmp_path / "storage"), preview_hashing_secret="x" * 40)
    issued = issue_preview(session, uuid.UUID("a1111111-1111-1111-1111-111111111111"), clip, storage, settings)
    grant = session.get(PreviewGrant, issued.grant.id)
    assert grant is not None and issued.raw_token not in grant.token_hash and issued.raw_token not in str(grant.__dict__)
    _, asset, _, _ = validate_grant(session, issued.grant.id, issued.raw_token, settings, count_access=True)
    assert list(stream_range(storage.open(asset.storage_key), 2, 5, 2)) == [b"23", b"45"]
    assert parse_range("bytes=-3", 10) == (7, 9)
    assert parse_range("bytes=3-", 10) == (3, 9)
    with pytest.raises(PreviewError):
        parse_range("bytes=99-100", 10)


def test_preview_rejects_expired_revoked_and_deleted_assets(session, tmp_path):  # type: ignore[no-untyped-def]
    clip, storage = prepared_clip(session, tmp_path)
    settings = Settings(local_storage_root=str(tmp_path / "storage"), preview_hashing_secret="x" * 40)
    issued = issue_preview(session, None, clip, storage, settings)
    issued.grant.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    with pytest.raises(PreviewError):
        validate_grant(session, issued.grant.id, issued.raw_token, settings)
    replacement = issue_preview(session, None, clip, storage, settings, refresh=True)
    asset = session.get(MediaAsset, replacement.grant.media_asset_id)
    assert asset is not None
    asset.lifecycle_state = "DELETED"
    session.commit()
    with pytest.raises(PreviewError):
        validate_grant(session, replacement.grant.id, replacement.raw_token, settings)


def test_cleanup_is_dry_run_then_preserves_deleted_record(session, tmp_path):  # type: ignore[no-untyped-def]
    clip, storage = prepared_clip(session, tmp_path)
    settings = Settings(local_storage_root=str(tmp_path / "storage"), preview_hashing_secret="x" * 40)
    issued = issue_preview(session, None, clip, storage, settings)
    asset = session.get(MediaAsset, issued.grant.media_asset_id)
    assert asset is not None
    asset.asset_type = "PREVIEW_PROXY"
    asset.retention_deadline = datetime.now(UTC) - timedelta(seconds=1)
    issued.grant.revoked_at = datetime.now(UTC)
    session.commit()
    assert cleanup_expired_media(session, storage, settings, dry_run=True).selected == 1
    result = cleanup_expired_media(session, storage, settings, dry_run=False)
    assert result.deleted == 1
    assert session.get(MediaAsset, asset.id).lifecycle_state == "DELETED"  # type: ignore[union-attr]
