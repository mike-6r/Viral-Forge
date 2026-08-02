import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.brands.service import ensure_legacy_brand
from app.common.config import Settings
from app.ingestion.storage import LocalFilesystemStorage
from app.production.models import ProductionClip, ProductionProject
from app.production.service import ProductionError
from app.rendered_media.models import RenderedMediaInspectionStatus
from app.rendered_media.service import (
    execute_inspection,
    request_inspection,
    review_inspection,
)
from tests.conftest import DEV_ACTOR_ID


def _rendered_clip(session, storage: LocalFilesystemStorage) -> ProductionClip:  # type: ignore[no-untyped-def]
    brand = ensure_legacy_brand(session)
    session.flush()
    temporary = storage.create_temporary()
    storage.write_chunk(temporary, b"controlled fixture media")
    stored = storage.finalize(temporary, ".mp4")
    project = ProductionProject(
        brand_id=brand.id,
        source_url="https://example.test/rendered-quality-fixture",
        source_title="Controlled media fixture",
        source_duration_seconds=10.0,
        status="SOURCE_READY",
        created_actor_id=DEV_ACTOR_ID,
    )
    session.add(project)
    session.flush()
    clip = ProductionClip(
        project_id=project.id,
        brand_id=brand.id,
        clip_number=1,
        start_seconds=0.0,
        end_seconds=10.0,
        duration_seconds=10.0,
        storage_key=stored.key,
        render_status="SUCCEEDED",
    )
    session.add(clip)
    session.commit()
    return clip


def _runner(command, **kwargs):  # type: ignore[no-untyped-def]
    if "-show_streams" in command:
        payload = {
            "format": {"duration": "10.0"},
            "streams": [
                {"codec_type": "video", "width": 1080, "height": 1920, "codec_name": "h264", "duration": "10.0"},
                {"codec_type": "audio", "codec_name": "aac", "duration": "10.0"},
            ],
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
    if "rawvideo" in command:
        return SimpleNamespace(returncode=0, stdout=bytes([32]) * (64 * 36), stderr=b"")
    return SimpleNamespace(returncode=0, stdout="", stderr="mean_volume: -18.0 dB\nmax_volume: -3.0 dB")


def test_inspection_is_idempotent_and_advisory(session, tmp_path: Path):  # type: ignore[no-untyped-def]
    storage = LocalFilesystemStorage(tmp_path / "storage")
    clip = _rendered_clip(session, storage)
    first = request_inspection(session, DEV_ACTOR_ID, clip, storage)
    assert request_inspection(session, DEV_ACTOR_ID, clip, storage).id == first.id
    completed = execute_inspection(session, DEV_ACTOR_ID, first, storage, runner=_runner)
    assert completed.status == RenderedMediaInspectionStatus.COMPLETED, completed.failure_category
    assert completed.overall_score is not None
    assert session.get(ProductionClip, clip.id).approval_status == "PENDING"
    assert session.get(ProductionClip, clip.id).publication_status == "NOT_QUEUED"


def test_rerun_stales_history_and_review_uses_optimistic_locking(session, tmp_path: Path):  # type: ignore[no-untyped-def]
    storage = LocalFilesystemStorage(tmp_path / "storage")
    clip = _rendered_clip(session, storage)
    first = request_inspection(session, DEV_ACTOR_ID, clip, storage)
    second = request_inspection(session, DEV_ACTOR_ID, clip, storage, rerun=True)
    assert second.inspection_version == first.inspection_version + 1
    assert first.status == RenderedMediaInspectionStatus.STALE
    with pytest.raises(ProductionError, match="reload"):
        review_inspection(session, DEV_ACTOR_ID, second, second.review_version + 1, True)
    decided = review_inspection(session, DEV_ACTOR_ID, second, second.review_version, True)
    assert decided.review_status == "APPROVED"
    assert session.get(ProductionClip, clip.id).approval_status == "PENDING"


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="FFmpeg/FFprobe are required for the generated rendered-media fixture")
def test_generated_mp4_runs_the_real_local_inspector(session, tmp_path: Path):  # type: ignore[no-untyped-def]
    """Docker runs this real-tool fixture; it never uses sensitive source media."""
    storage = LocalFilesystemStorage(tmp_path / "storage")
    clip = _rendered_clip(session, storage)
    generated = tmp_path / "controlled.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=navy:s=1080x1920:d=1",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=1", "-shortest",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(generated),
    ], check=True, capture_output=True)
    temporary = storage.create_temporary()
    storage.write_chunk(temporary, generated.read_bytes())
    stored = storage.finalize(temporary, ".mp4")
    clip.storage_key = stored.key
    session.commit()
    settings = Settings(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe", video_work_root=str(tmp_path / "work"))
    item = request_inspection(session, DEV_ACTOR_ID, clip, storage, settings=settings)
    completed = execute_inspection(session, DEV_ACTOR_ID, item, storage, settings=settings)
    assert completed.status == RenderedMediaInspectionStatus.COMPLETED
    assert completed.evidence_json["sampling"]["frames_persisted"] is False
