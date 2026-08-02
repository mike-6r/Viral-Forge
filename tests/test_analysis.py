import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.analysis.models import (
    AnalysisEvent,
    AnalysisSegment,
    AnalysisStatus,
    TranscriptSegment,
)
from app.analysis.service import (
    LocalMediaAnalyzer,
    TechnicalAnalysis,
    TimedText,
    execute_analysis,
    normalize_timeline,
    request_analysis,
)
from app.common.config import Settings
from app.ingestion.storage import LocalFilesystemStorage
from app.production.models import ProductionProject
from app.production.service import ProductionError
from tests.conftest import DEV_ACTOR_ID


class FakeVideoAnalyzer:
    def analyze(self, path: Path) -> TechnicalAnalysis:
        assert path.is_file()
        return TechnicalAnalysis(
            duration_seconds=12.0,
            fps=30.0,
            width=1920,
            height=1080,
            frame_count=360,
            bitrate=2_000_000,
            codec="h264",
            audio_channels=2,
            segments=[
                (0.0, 4.0, "SCENE", 0.8, {"scene_change": True}),
                (4.0, 6.0, "LOUD_AUDIO", 0.7, {"rms": -10.0}),
                (6.0, 12.0, "MOTION", 0.6, {"motion": "detected"}),
            ],
            events=[(4.0, "SHOT_CHANGE", 0.8, {})],
        )


class FakeTranscription:
    def transcribe(self, path: Path) -> tuple[str | None, list[TimedText]]:
        assert path.is_file()
        return "en", [TimedText(0.0, 2.0, "A safe test transcript.", "speaker-1", 0.9)]


class FailingOcr:
    def detect(self, path: Path) -> list[tuple[float, str, float | None]]:
        raise RuntimeError("optional provider unavailable")


def test_analysis_is_reusable_and_optional_provider_failure_is_nonfatal(session, tmp_path):  # type: ignore[no-untyped-def]
    storage = LocalFilesystemStorage(tmp_path / "storage")
    temporary = storage.create_temporary()
    storage.write_chunk(temporary, b"mock source bytes")
    stored = storage.finalize(temporary, ".mp4")
    project = ProductionProject(
        source_url="https://youtu.be/Analysis0001",
        source_storage_key=stored.key,
        created_actor_id=DEV_ACTOR_ID,
    )
    session.add(project)
    session.commit()

    queued = request_analysis(session, DEV_ACTOR_ID, project)
    assert request_analysis(session, DEV_ACTOR_ID, project).id == queued.id
    result = execute_analysis(
        session,
        DEV_ACTOR_ID,
        queued,
        storage,
        analyzer=FakeVideoAnalyzer(),
        transcription=FakeTranscription(),
        ocr=FailingOcr(),
        settings=Settings(video_work_root=str(tmp_path / "work")),
    )

    assert result.status == AnalysisStatus.COMPLETED
    assert result.transcript_language == "en"
    assert result.metadata_json["ocr_error"] == "RuntimeError"
    assert len(list(session.scalars(select(AnalysisSegment)))) == 3
    assert len(list(session.scalars(select(TranscriptSegment)))) == 1
    assert len(list(session.scalars(select(AnalysisEvent)))) == 1
    assert execute_analysis(session, DEV_ACTOR_ID, result, storage).id == result.id


def test_analysis_requires_downloaded_source(session):  # type: ignore[no-untyped-def]
    project = ProductionProject(
        source_url=f"https://youtu.be/{uuid.uuid4().hex[:11]}",
        created_actor_id=DEV_ACTOR_ID,
    )
    session.add(project)
    session.commit()
    with pytest.raises(ProductionError, match="download the source"):
        request_analysis(session, DEV_ACTOR_ID, project)


def test_analysis_rerun_is_rejected_while_running(session):  # type: ignore[no-untyped-def]
    project = ProductionProject(
        source_url=f"https://youtu.be/{uuid.uuid4().hex[:11]}",
        source_storage_key="assets/mock-source.mp4",
        created_actor_id=DEV_ACTOR_ID,
    )
    session.add(project)
    session.commit()
    analysis = request_analysis(session, DEV_ACTOR_ID, project)
    analysis.status = AnalysisStatus.RUNNING
    session.commit()
    with pytest.raises(ProductionError, match="already running"):
        request_analysis(session, DEV_ACTOR_ID, project, rerun=True)


def test_analysis_api_reports_not_ready_source(client):  # type: ignore[no-untyped-def]
    headers = {"X-Development-Actor": str(DEV_ACTOR_ID)}
    created = client.post(
        "/api/v1/production/projects",
        headers=headers,
        json={"source_url": "https://youtu.be/AnalysisApi1"},
    )
    response = client.post(f"/api/v1/production/projects/{created.json()['id']}/analysis", headers=headers)
    assert response.status_code == 409


def test_analysis_api_queues_and_lists_status(client, session):  # type: ignore[no-untyped-def]
    headers = {"X-Development-Actor": str(DEV_ACTOR_ID)}
    project = ProductionProject(
        source_url="https://youtu.be/AnalysisApi2",
        source_storage_key="assets/mock-source.mp4",
        created_actor_id=DEV_ACTOR_ID,
    )
    session.add(project)
    session.commit()
    queued = client.post(f"/api/v1/production/projects/{project.id}/analysis", headers=headers)
    assert queued.status_code == 202, queued.text
    analysis_id = queued.json()["id"]
    assert queued.json()["status"] == AnalysisStatus.QUEUED
    assert client.get(f"/api/v1/analysis/{analysis_id}", headers=headers).json()["id"] == analysis_id
    assert len(client.get(f"/api/v1/production/projects/{project.id}/analysis", headers=headers).json()) == 1
    cancelled = client.post(f"/api/v1/analysis/{analysis_id}/cancel", headers=headers)
    assert cancelled.json()["status"] == AnalysisStatus.CANCELLED


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="FFmpeg/FFprobe are required for the generated media fixture")
def test_real_local_media_analyzer_detects_bounded_signals(tmp_path):
    video = tmp_path / "controlled.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=320x180:d=2:r=24",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=320x180:d=2:r=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=8000:duration=1",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=8000:cl=mono:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:sample_rate=8000:duration=2",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v];[2:a][3:a][4:a]concat=n=3:v=0:a=1[a]",
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(video),
        ],
        check=True,
        capture_output=True,
    )
    result = LocalMediaAnalyzer(
        Settings(
            analysis_scene_detection_threshold=0.05,
            analysis_audio_loudness_threshold_db=-40,
            analysis_audio_peak_threshold_db=-20,
            analysis_motion_high_threshold=0.02,
            analysis_motion_low_threshold=0.005,
            analysis_timeout_seconds=60,
        )
    ).analyze(video)
    kinds = {segment[2] for segment in result.segments}
    event_kinds = {event[1] for event in result.events}
    assert {"SILENCE", "SPEECH", "SCENE", "LOUD_AUDIO", "MOTION"} <= kinds
    assert {"SHOT_CHANGE", "AUDIO_PEAK", "MOTION_SPIKE"} <= event_kinds


def test_timeline_normalization_deduplicates_and_caps_events():
    segments, events, warnings = normalize_timeline(
        10.0,
        [(0.0, 2.0, "SPEECH", 1.0, {}), (-1.0, 2.0, "SPEECH", 1.0, {})],
        [(1.0, "AUDIO_PEAK", 1.0, {}), (1.0, "AUDIO_PEAK", 1.0, {}), (2.0, "SHOT_CHANGE", 1.0, {})],
        1,
    )
    assert len(segments) == 1 and len(events) == 1 and warnings


def test_analysis_versions_preserve_foundation_record(session):  # type: ignore[no-untyped-def]
    project = ProductionProject(
        source_url="https://youtu.be/VersionedAnalysis",
        source_storage_key="assets/mock-source.mp4",
        created_actor_id=DEV_ACTOR_ID,
    )
    session.add(project)
    session.commit()
    foundation = request_analysis(session, DEV_ACTOR_ID, project, analysis_version="foundation-v1")
    real = request_analysis(session, DEV_ACTOR_ID, project, analysis_version="real-media-v1")
    assert foundation.id != real.id
    assert request_analysis(session, DEV_ACTOR_ID, project, analysis_version="real-media-v1").id == real.id
