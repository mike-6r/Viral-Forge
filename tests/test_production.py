import asyncio
import json
import subprocess
import sys
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.audit.models import AuditEvent
from app.common.config import Settings
from app.production.models import PostingQueueItem, ProductionClip
from app.production.service import (
    ProductionError,
    YtDlpDownloadProvider,
    decide_clip,
    ffmpeg_clip_command,
    fixed_segments,
    probe_video,
    youtube_video_id,
)
from app.production.youtube import resolve_youtube_channel, youtube_channel_reference
from tests.conftest import DEV_ACTOR_ID


def test_youtube_url_segments_and_vertical_command(tmp_path):
    assert youtube_video_id("https://www.youtube.com/watch?v=AbCdEf_1234") == "AbCdEf_1234"
    assert fixed_segments(100, 45, 15) == [(0.0, 45.0), (45.0, 90.0)]
    command = ffmpeg_clip_command(tmp_path / "source.mp4", tmp_path / "clip.mp4", 0, 45)
    assert (
        Path(command[0]).stem == "ffmpeg"
        and "boxblur" in command[command.index("-filter_complex") + 1]
    )
    with pytest.raises(ProductionError):
        youtube_video_id("https://example.test/video")


def test_ytdlp_provider_emits_newline_progress_without_logging_it(tmp_path):
    destination = tmp_path / "download"
    progress: list[str] = []

    class Process:
        stdout = ["[download]   5.0% of 10.00MiB\n", "[download] 100.0% of 10.00MiB\n"]

        def wait(self, timeout: float) -> int:
            return 0

        def kill(self) -> None:
            return None

    def popen(*_: object, **__: object) -> Process:
        destination.with_suffix(".mp4").write_bytes(b"bounded test media")
        return Process()

    provider = YtDlpDownloadProvider(
        Settings(video_download_executable=sys.executable), popen=popen  # type: ignore[arg-type]
    )
    result = provider.download(
        "https://www.youtube.com/watch?v=AbCdEf_1234", destination, progress.append
    )
    assert result == destination.with_suffix(".mp4")
    assert progress == ["[download]   5.0% of 10.00MiB\n", "[download] 100.0% of 10.00MiB\n", "completed"]


def test_youtube_channel_reference_accepts_only_public_channel_forms():
    assert youtube_channel_reference("https://www.youtube.com/channel/UC123") == ("id", "UC123")
    assert youtube_channel_reference("https://youtube.com/@PhoenixPolice") == (
        "handle",
        "PhoenixPolice",
    )
    assert youtube_channel_reference("@PhoenixPolice") == ("handle", "PhoenixPolice")
    with pytest.raises(ProductionError, match="channel URL"):
        youtube_channel_reference("https://example.test/channel")
    with pytest.raises(ProductionError, match="channel URL"):
        youtube_channel_reference("https://example.test/channel/UC123")


def test_resolve_youtube_channel_uses_only_official_api_metadata():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/channels"):
            assert request.url.params["forHandle"] == "PhoenixPolice"
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "UCofficial",
                            "snippet": {
                                "title": "Phoenix Police",
                                "thumbnails": {"medium": {"url": "https://example.test/channel.jpg"}},
                            },
                            "statistics": {"videoCount": "42"},
                        }
                    ]
                },
            )
        if request.url.path.endswith("/search"):
            assert request.url.params["channelId"] == "UCofficial"
            return httpx.Response(200, json={"items": [{"id": {"videoId": "latest"}}]})
        if request.url.path.endswith("/videos"):
            return httpx.Response(
                200,
                json={"items": [{"id": "latest", "snippet": {"title": "Latest public upload"}}]},
            )
        raise AssertionError(f"unexpected endpoint: {request.url}")

    async def resolve() -> object:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await resolve_youtube_channel(
                "@PhoenixPolice", Settings(youtube_api_key="test-key"), client
            )

    channel = asyncio.run(resolve())
    assert channel.channel_id == "UCofficial"
    assert channel.title == "Phoenix Police"
    assert channel.video_count == 42
    assert channel.latest_upload_title == "Latest public upload"


def test_resolve_youtube_channel_keeps_valid_channel_when_optional_preview_fails():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/channels"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "UCofficial",
                            "snippet": {"title": "Police Activity", "thumbnails": {}},
                            "statistics": {"videoCount": "42"},
                        }
                    ]
                },
            )
        if request.url.path.endswith("/search"):
            return httpx.Response(429, json={"error": {"message": "quota exceeded"}})
        raise AssertionError(f"unexpected endpoint: {request.url}")

    async def resolve() -> object:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await resolve_youtube_channel(
                "https://www.youtube.com/@PoliceActivity", Settings(youtube_api_key="test-key"), client
            )

    channel = asyncio.run(resolve())
    assert channel.channel_id == "UCofficial"
    assert channel.title == "Police Activity"
    assert channel.latest_upload_title is None


def test_probe_and_approval_queue(session, tmp_path):  # type: ignore[no-untyped-def]
    def runner(*_: object, **__: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps(
                {
                    "format": {"duration": "90"},
                    "streams": [
                        {
                            "codec_type": "video",
                            "width": 1920,
                            "height": 1080,
                            "codec_name": "h264",
                            "r_frame_rate": "30/1",
                        },
                        {"codec_type": "audio", "codec_name": "aac"},
                    ],
                }
            ),
            stderr="",
        )

    probe = probe_video(tmp_path / "video.mp4", runner=runner)  # type: ignore[arg-type]
    assert probe.duration_seconds == 90 and probe.audio_codec == "aac"
    clip = ProductionClip(
        project_id=DEV_ACTOR_ID,
        clip_number=1,
        start_seconds=0,
        end_seconds=45,
        duration_seconds=45,
        render_status="SUCCEEDED",
    )
    session.add(clip)
    session.commit()
    decide_clip(session, DEV_ACTOR_ID, clip, True)
    assert (
        session.scalar(select(PostingQueueItem).where(PostingQueueItem.clip_id == clip.id))
        is not None
    )
    decide_clip(session, DEV_ACTOR_ID, clip, True)
    assert (
        len(
            list(
                session.scalars(select(PostingQueueItem).where(PostingQueueItem.clip_id == clip.id))
            )
        )
        == 1
    )
    assert (
        session.scalar(
            select(AuditEvent).where(
                AuditEvent.entity_id == clip.id, AuditEvent.event_name == "production.clip.approved"
            )
        )
        is not None
    )
    assert (
        len(
            list(
                session.scalars(
                    select(AuditEvent).where(
                        AuditEvent.entity_id == clip.id,
                        AuditEvent.event_name == "production.clip.approved",
                    )
                )
            )
        )
        == 1
    )
    decide_clip(session, DEV_ACTOR_ID, clip, False)
    assert (
        session.scalar(select(PostingQueueItem).where(PostingQueueItem.clip_id == clip.id)) is None
    )


def test_production_api_project_and_review(client, session):  # type: ignore[no-untyped-def]
    headers = {"X-Development-Actor": str(DEV_ACTOR_ID)}
    project = client.post(
        "/api/v1/production/projects",
        headers=headers,
        json={"source_url": "https://youtu.be/AbCdEf_1234"},
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    clip = ProductionClip(
        project_id=uuid.UUID(project_id),
        clip_number=1,
        start_seconds=0,
        end_seconds=45,
        duration_seconds=45,
        render_status="SUCCEEDED",
    )
    session.add(clip)
    session.commit()
    assert (
        client.post(f"/api/v1/production/clips/{clip.id}/approve", headers=headers).json()[
            "approval_status"
        ]
        == "APPROVED"
    )
    assert len(client.get("/api/v1/production/queue", headers=headers).json()) == 1
