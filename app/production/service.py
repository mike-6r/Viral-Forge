"""Small synchronous building blocks for the operator-authorized clipping workflow."""

import json
import re
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from typing import Protocol
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.common.config import Settings, get_settings
from app.common.errors import DomainError
from app.ingestion.storage import LocalFilesystemStorage
from app.production.models import (
    PostingQueueItem,
    ProductionClip,
    ProductionProject,
    ProductionSource,
)
from app.production.source_quality import (
    SourceMetadata,
    file_fingerprint,
    metadata_payload,
    source_fingerprint,
)
from app.production.source_resolver import OriginalSourceResolver, ResolvedCandidate

Runner = Callable[..., subprocess.CompletedProcess[str]]
PopenRunner = Callable[..., subprocess.Popen[str]]
_YTDLP_PROGRESS = re.compile(r"\[download\]\s+(?P<percent>\d+(?:\.\d+)?)%")


class ProductionError(DomainError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class VideoProbe:
    duration_seconds: float
    width: int
    height: int
    video_codec: str
    audio_codec: str | None
    frame_rate: str


class VideoDownloadProvider(Protocol):
    def download(
        self, url: str, destination: Path, progress_callback: Callable[[str], None] | None = None
    ) -> Path: ...


def youtube_video_id(url: str) -> str:
    parsed = urlsplit(url.strip())
    host = parsed.hostname.lower() if parsed.hostname else ""
    if host in {"youtu.be", "www.youtu.be"}:
        candidate = parsed.path.strip("/")
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com"} and parsed.path == "/watch":
        candidate = parse_qs(parsed.query).get("v", [""])[0]
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com"} and parsed.path.startswith(
        "/shorts/"
    ):
        candidate = parsed.path.split("/")[2] if len(parsed.path.split("/")) > 2 else ""
    else:
        raise ProductionError("INVALID_YOUTUBE_URL", "a YouTube video URL is required")
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,32}", candidate):
        raise ProductionError("INVALID_YOUTUBE_URL", "a YouTube video URL is required")
    return candidate


class YtDlpDownloadProvider:
    """An optional local executable provider; it never receives cookies or a shell."""

    def __init__(
        self,
        settings: Settings | None = None,
        runner: Runner = subprocess.run,
        popen: PopenRunner = subprocess.Popen,
    ) -> None:
        self.settings = settings or get_settings()
        self.runner = runner
        self.popen = popen

    def command_prefix(self) -> list[str]:
        configured = self.settings.ytdlp_path or self.settings.video_download_executable
        if configured and (Path(configured).is_file() or shutil.which(configured)):
            return [configured]
        try:
            __import__("yt_dlp")
        except ImportError as error:
            raise ProductionError(
                "YTDLP_UNAVAILABLE", "yt-dlp is not installed or configured"
            ) from error
        return [sys.executable, "-m", "yt_dlp"]

    def download(
        self, url: str, destination: Path, progress_callback: Callable[[str], None] | None = None
    ) -> Path:
        youtube_video_id(url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        output_template = str(destination.with_suffix(".%(ext)s"))
        command = [
            *self.command_prefix(),
            "--no-playlist",
            "--no-cookies",
            "--no-warnings",
            "--max-filesize",
            str(self.settings.video_download_max_bytes),
            "--recode-video",
            "mp4",
            "--output",
            output_template,
            url,
        ]
        try:
            if progress_callback is None:
                completed = self.runner(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=self.settings.video_download_timeout_seconds,
                    check=True,
                )
            else:
                # --newline emits bounded, parseable yt-dlp progress lines. It
                # avoids shell execution and keeps provider output out of logs.
                process = self.popen(
                    [*command[:-1], "--newline", command[-1]],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                stdout = process.stdout
                if stdout is None:
                    raise OSError("yt-dlp did not provide a progress stream")
                lines: Queue[str | None] = Queue()

                def _read_output() -> None:
                    for line in stdout:
                        lines.put(line)
                    lines.put(None)

                reader = Thread(target=_read_output, daemon=True)
                reader.start()
                deadline = time.monotonic() + self.settings.video_download_timeout_seconds
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        process.kill()
                        raise subprocess.TimeoutExpired(command, self.settings.video_download_timeout_seconds)
                    try:
                        line = lines.get(timeout=min(1.0, remaining))
                    except Empty:
                        if process.poll() is None:
                            continue
                        line = None
                    if line is None:
                        break
                    progress_callback(line)
                return_code = process.wait(timeout=max(1.0, deadline - time.monotonic()))
                if return_code:
                    raise subprocess.CalledProcessError(return_code, command)
                completed = subprocess.CompletedProcess(command, return_code)
        except (OSError, subprocess.SubprocessError) as error:
            for candidate in destination.parent.glob(f"{destination.stem}.*"):
                candidate.unlink(missing_ok=True)
            raise ProductionError("DOWNLOAD_FAILED", "authorized video download failed") from error
        if progress_callback is not None:
            progress_callback("completed" if completed.returncode == 0 else "failed")
        final_path = destination.with_suffix(".mp4")
        if (
            not final_path.is_file()
            or final_path.stat().st_size > self.settings.video_download_max_bytes
        ):
            final_path.unlink(missing_ok=True)
            raise ProductionError("DOWNLOAD_FAILED", "download did not produce a bounded MP4")
        return final_path


def probe_video(
    path: Path, settings: Settings | None = None, runner: Runner = subprocess.run
) -> VideoProbe:
    settings = settings or get_settings()
    command = [
        settings.ffprobe_path,
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = runner(command, capture_output=True, text=True, timeout=30, check=True)
        payload = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError) as error:
        raise ProductionError("INVALID_VIDEO", "video could not be read") from error
    streams = payload.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    try:
        if not isinstance(video, dict):
            raise KeyError("video")
        duration = float(payload["format"]["duration"])
        probe = VideoProbe(
            duration,
            int(video["width"]),
            int(video["height"]),
            str(video["codec_name"]),
            str(audio["codec_name"]) if audio else None,
            str(video.get("r_frame_rate", "0/1")),
        )
    except (TypeError, ValueError, KeyError) as error:
        raise ProductionError("INVALID_VIDEO", "video has no usable video stream") from error
    if probe.duration_seconds <= 0 or probe.duration_seconds > settings.max_source_duration_seconds:
        raise ProductionError("INVALID_VIDEO", "video duration is not permitted")
    return probe


def fixed_segments(
    duration: float, clip_duration: int, minimum_duration: int, overlap: int = 0
) -> list[tuple[float, float]]:
    if clip_duration < minimum_duration or overlap >= clip_duration or duration <= 0:
        raise ValueError("invalid clip timing configuration")
    segments: list[tuple[float, float]] = []
    start = 0.0
    while start < duration:
        end = min(start + clip_duration, duration)
        if end - start >= minimum_duration:
            segments.append((start, end))
        start += clip_duration - overlap
    return segments


def ffmpeg_clip_command(
    input_path: Path,
    output_path: Path,
    start: float,
    duration: float,
    settings: Settings | None = None,
) -> list[str]:
    settings = settings or get_settings()
    width, height = settings.output_width, settings.output_height
    filters = f"[0:v]split[fg][bg];[bg]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},boxblur=20:1[blur];[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[main];[blur][main]overlay=(W-w)/2:(H-h)/2,setsar=1[v]"
    return [
        settings.ffmpeg_path,
        "-y",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(input_path),
        "-filter_complex",
        filters,
        "-map",
        "[v]",
        "-map",
        "0:a?",
        "-c:v",
        settings.output_video_codec,
        "-r",
        str(settings.output_fps),
        "-c:a",
        settings.output_audio_codec,
        "-af",
        "loudnorm",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def _work_path(project_id: uuid.UUID, suffix: str, settings: Settings) -> Path:
    root = Path(settings.video_work_root).resolve()
    path = root / project_id.hex / suffix
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _source_from_candidate(
    project_id: uuid.UUID,
    brand_id: uuid.UUID,
    candidate: ResolvedCandidate,
    parent_source_id: uuid.UUID | None = None,
) -> ProductionSource:
    """Map a resolver candidate to persistence without accepting opaque provider data."""
    metadata = candidate.metadata
    watermark = candidate.watermark
    quality = candidate.quality
    return ProductionSource(
        project_id=project_id,
        brand_id=brand_id,
        parent_source_id=parent_source_id,
        platform=metadata.platform[:50],
        source_url=metadata.source_url[:2048],
        resolved_media_url=metadata.resolved_media_url[:2048]
        if metadata.resolved_media_url
        else None,
        uploader_name=metadata.uploader_name,
        uploader_account_id=metadata.uploader_account_id,
        account_url=metadata.account_url,
        video_title=metadata.video_title,
        description=metadata.description,
        upload_date=metadata.upload_date,
        duration_seconds=metadata.duration_seconds,
        width=metadata.width,
        height=metadata.height,
        frame_rate=metadata.frame_rate,
        bitrate=metadata.bitrate,
        file_size_bytes=metadata.file_size_bytes,
        view_count=metadata.view_count,
        ownership_classification=candidate.ownership,
        official_source_confidence=round(
            1 - candidate.repost_likelihood if candidate.ownership.startswith("OFFICIAL") else 0.0,
            2,
        ),
        original_source_confidence=candidate.original_confidence,
        repost_likelihood=candidate.repost_likelihood,
        watermark_status=watermark.status,
        watermark_confidence=watermark.confidence,
        watermark_regions=watermark.regions,
        quality_score=quality.score,
        quality_components=quality.components,
        warnings=quality.warnings,
        selected_source_reason=candidate.reason,
        quality_status=quality.quality_status,
        metadata_json=metadata_payload(metadata),
        fingerprint_json=source_fingerprint(metadata),
    )


def resolve_project_sources(
    session: Session,
    actor_id: uuid.UUID,
    project: ProductionProject,
    resolver: OriginalSourceResolver | None = None,
) -> ProductionProject:
    """Persist every considered source and make the automatic selection auditable."""
    from app.production.source_quality import QualityResult, WatermarkResult, now_iso

    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="production_project",
            entity_id=project.id,
            event_name="production.source.submitted",
            payload={"source_url": project.source_url},
        )
    )
    try:
        resolution = (resolver or OriginalSourceResolver()).resolve(project.source_url)
    except ProductionError as error:
        metadata = SourceMetadata(
            project.source_platform,
            project.source_url,
            uploader_name=project.source_channel,
            video_title=project.source_title,
            metadata_json={"resolution_error": error.code},
        )
        quality = QualityResult(
            0.0,
            {},
            ["Metadata could not be resolved automatically; manual source review required."],
            "LOWER_QUALITY",
            "Metadata resolution failed.",
        )
        fallback = ResolvedCandidate(
            metadata,
            "UNKNOWN",
            WatermarkResult(
                "MANUAL_REVIEW_REQUIRED", 0.0, [], "No reliable metadata was available.", []
            ),
            quality,
            0.0,
            0.5,
            "Submitted source retained because metadata resolution failed.",
        )
        candidates = [fallback]
        needs_manual = True
        session.add(
            AuditEvent(
                actor_id=actor_id,
                entity_type="production_project",
                entity_id=project.id,
                event_name="production.source.metadata_failed",
                payload={"code": error.code},
            )
        )
    else:
        candidates = resolution.candidates
        needs_manual = resolution.needs_manual_review
        session.add(
            AuditEvent(
                actor_id=actor_id,
                entity_type="production_project",
                entity_id=project.id,
                event_name="production.source.metadata_resolved",
                payload={"candidate_count": len(candidates), "query": resolution.query},
            )
        )
    persisted: list[ProductionSource] = []
    for candidate in candidates:
        record = _source_from_candidate(project.id, project.brand_id, candidate)
        record.discovered_at = now_iso()
        session.add(record)
        persisted.append(record)
    session.flush()
    submitted_record = next(
        (record for record in persisted if record.source_url == project.source_url), None
    )
    if submitted_record is not None:
        for record in persisted:
            if record.id != submitted_record.id:
                record.parent_source_id = submitted_record.id
    selected = persisted[0]
    project.selected_source_id = selected.id
    project.source_platform = selected.platform
    project.source_title = selected.video_title or project.source_title
    project.source_channel = selected.uploader_name or project.source_channel
    project.source_duration_seconds = selected.duration_seconds
    project.status = "SOURCE_REVIEW_REQUIRED" if needs_manual else "SOURCE_RESOLVED"
    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="production_project",
            entity_id=project.id,
            event_name="production.source.candidates_ranked",
            payload={"candidate_ids": [str(record.id) for record in persisted]},
        )
    )
    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="production_project",
            entity_id=project.id,
            event_name="production.source.selected_automatically",
            payload={"source_id": str(selected.id), "reason": selected.selected_source_reason},
        )
    )
    session.commit()
    return project


def create_project(
    session: Session,
    actor_id: uuid.UUID,
    source_url: str,
    title: str | None = None,
    channel: str | None = None,
    brand_id: uuid.UUID | None = None,
    resolver: OriginalSourceResolver | None = None,
) -> ProductionProject:
    video_id = youtube_video_id(source_url)
    from app.brands.service import ensure_legacy_brand

    resolved_brand_id = brand_id or ensure_legacy_brand(session).id
    existing = session.scalar(
        select(ProductionProject).where(
            ProductionProject.source_url == source_url,
            ProductionProject.brand_id == resolved_brand_id,
        )
    )
    if existing is not None:
        return existing
    project = ProductionProject(
        brand_id=resolved_brand_id,
        source_url=source_url,
        source_video_id=video_id,
        source_title=title,
        source_channel=channel,
        created_actor_id=actor_id,
    )
    session.add(project)
    session.flush()
    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="production_project",
            entity_id=project.id,
            brand_id=project.brand_id,
            event_name="production.project.created",
            payload={"video_id": video_id},
        )
    )
    session.commit()
    return resolve_project_sources(session, actor_id, project, resolver)


def selected_source(session: Session, project: ProductionProject) -> ProductionSource:
    source = (
        session.get(ProductionSource, project.selected_source_id)
        if project.selected_source_id
        else None
    )
    if source is None:
        raise ProductionError("SOURCE_NOT_RESOLVED", "source candidates have not been resolved")
    return source


def accept_source(
    session: Session, actor_id: uuid.UUID, project: ProductionProject
) -> ProductionProject:
    source = selected_source(session, project)
    if project.status == "SOURCE_REJECTED":
        raise ProductionError("SOURCE_REJECTED", "a rejected source cannot be accepted")
    if project.status == "SOURCE_ACCEPTED":
        return project
    project.status = "SOURCE_ACCEPTED"
    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="production_project",
            entity_id=project.id,
            event_name="production.source.accepted",
            payload={"source_id": str(source.id)},
        )
    )
    session.commit()
    return project


def reject_source(
    session: Session, actor_id: uuid.UUID, project: ProductionProject
) -> ProductionProject:
    if project.status == "SOURCE_REJECTED":
        return project
    project.status = "SOURCE_REJECTED"
    project.source_decision_version += 1
    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="production_project",
            entity_id=project.id,
            event_name="production.source.rejected",
        )
    )
    session.commit()
    return project


def choose_source(
    session: Session,
    actor_id: uuid.UUID,
    project: ProductionProject,
    source_id: uuid.UUID,
    expected_version: int,
) -> ProductionProject:
    if expected_version != project.source_decision_version:
        if project.selected_source_id == source_id:
            return project
        raise ProductionError(
            "STALE_SOURCE_ACTION", "source selection changed; reopen the candidate list"
        )
    source = session.get(ProductionSource, source_id)
    if source is None or source.project_id != project.id:
        raise ProductionError(
            "SOURCE_CANDIDATE_NOT_FOUND", "candidate does not belong to this project"
        )
    if project.selected_source_id == source.id:
        return project
    if project.source_storage_key or session.scalar(
        select(ProductionClip).where(ProductionClip.project_id == project.id)
    ):
        raise ProductionError(
            "SOURCE_ALREADY_PROCESSED", "cannot replace a source after download or clip generation"
        )
    project.selected_source_id = source.id
    (
        project.source_platform,
        project.source_title,
        project.source_channel,
        project.source_duration_seconds,
    ) = source.platform, source.video_title, source.uploader_name, source.duration_seconds
    project.source_decision_version += 1
    project.status = (
        "SOURCE_REVIEW_REQUIRED"
        if source.quality_status in {"WATERMARKED_REVIEW", "REPOST_SUSPECTED", "LOWER_QUALITY"}
        else "SOURCE_RESOLVED"
    )
    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="production_project",
            entity_id=project.id,
            event_name="production.source.changed_manually",
            payload={"source_id": str(source.id), "previous_version": expected_version},
        )
    )
    session.commit()
    return project


def attach_authorized_mp4(
    session: Session,
    actor_id: uuid.UUID,
    project: ProductionProject,
    source_path: Path,
    storage: LocalFilesystemStorage,
    settings: Settings | None = None,
    runner: Runner = subprocess.run,
) -> ProductionProject:
    settings = settings or get_settings()
    probe = probe_video(source_path, settings, runner)
    temporary = storage.create_temporary()
    try:
        with source_path.open("rb") as handle:
            while chunk := handle.read(262_144):
                storage.write_chunk(temporary, chunk)
        project.source_storage_key = storage.finalize(temporary, ".mp4").key
    except Exception:
        storage.delete(temporary)
        raise
    project.source_duration_seconds = probe.duration_seconds
    project.status = "SOURCE_READY"
    project.download_progress_percent = 100
    project.download_progress_stage = "READY"
    # Retention inventory is a compatibility layer: downloading/rendering still
    # owns the authoritative storage key and remains unchanged.
    from app.media_preview.service import ensure_source_asset

    ensure_source_asset(session, project, storage, settings)
    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="production_project",
            entity_id=project.id,
            event_name="production.source.attached",
            payload={"duration_seconds": probe.duration_seconds},
        )
    )
    session.commit()
    return project


def download_project(
    session: Session,
    actor_id: uuid.UUID,
    project: ProductionProject,
    storage: LocalFilesystemStorage,
    provider: VideoDownloadProvider | None = None,
    settings: Settings | None = None,
) -> ProductionProject:
    settings = settings or get_settings()
    if project.source_storage_key:
        return project
    if project.status not in {
        "SOURCE_RESOLVED",
        "SOURCE_ACCEPTED",
        "DOWNLOADING",
        "DOWNLOAD_FAILED",
    }:
        raise ProductionError(
            "SOURCE_REVIEW_REQUIRED", "accept or resolve the source before downloading"
        )
    if settings.video_download_provider != "yt_dlp":
        raise ProductionError(
            "DOWNLOAD_NOT_CONFIGURED", "no authorized download provider is configured"
        )
    path = _work_path(project.id, "download", settings)
    source = selected_source(session, project)
    project.status = "DOWNLOADING"
    project.download_progress_percent = 0
    project.download_progress_stage = "DOWNLOADING"
    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="production_project",
            entity_id=project.id,
            event_name="production.download.started",
            payload={"source_id": str(source.id)},
        )
    )
    session.commit()
    try:
        last_progress = -1

        def report_progress(line: str) -> None:
            nonlocal last_progress
            match = _YTDLP_PROGRESS.search(line)
            if match is None:
                return
            percent = max(0, min(100, int(float(match.group("percent")))))
            if percent == last_progress:
                return
            last_progress = percent
            project.download_progress_percent = percent
            project.download_progress_stage = "DOWNLOADING"
            session.commit()

        downloaded = (provider or YtDlpDownloadProvider(settings)).download(
            source.source_url, path, progress_callback=report_progress
        )
        project.download_progress_percent = 100
        project.download_progress_stage = "VERIFYING"
        session.commit()
        source.fingerprint_json = {
            **source.fingerprint_json,
            **file_fingerprint(
                downloaded,
                SourceMetadata(
                    source.platform,
                    source.source_url,
                    duration_seconds=source.duration_seconds,
                    width=source.width,
                    height=source.height,
                ),
            ),
        }
        result = attach_authorized_mp4(session, actor_id, project, downloaded, storage, settings)
        session.add(
            AuditEvent(
                actor_id=actor_id,
                entity_type="production_project",
                entity_id=project.id,
                event_name="production.download.completed",
                payload={"source_id": str(source.id)},
            )
        )
        session.commit()
        return result
    except ProductionError as error:
        project.status = "DOWNLOAD_FAILED"
        project.download_progress_stage = "FAILED"
        project.last_error = str(error)
        session.add(
            AuditEvent(
                actor_id=actor_id,
                entity_type="production_project",
                entity_id=project.id,
                event_name="production.download.failed",
                payload={"code": error.code},
            )
        )
        session.commit()
        raise
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


def _render_clip_from_source(
    project: ProductionProject,
    source_path: Path,
    clip_number: int,
    start: float,
    end: float,
    storage: LocalFilesystemStorage,
    settings: Settings,
    runner: Runner,
) -> ProductionClip:
    output_path = _work_path(project.id, f"clip-{clip_number}.mp4", settings)
    try:
        runner(
            ffmpeg_clip_command(source_path, output_path, start, end - start, settings),
            capture_output=True,
            text=True,
            timeout=max(60, int((end - start) * 10)),
            check=True,
        )
        temporary = storage.create_temporary()
        with output_path.open("rb") as handle:
            while chunk := handle.read(262_144):
                storage.write_chunk(temporary, chunk)
        key = storage.finalize(temporary, ".mp4").key
        return ProductionClip(
            project_id=project.id,
            brand_id=project.brand_id,
            clip_number=clip_number,
            start_seconds=start,
            end_seconds=end,
            duration_seconds=end - start,
            storage_key=key,
            render_status="SUCCEEDED",
        )
    except (OSError, subprocess.SubprocessError):
        return ProductionClip(
            project_id=project.id,
            brand_id=project.brand_id,
            clip_number=clip_number,
            start_seconds=start,
            end_seconds=end,
            duration_seconds=end - start,
            render_status="FAILED",
        )


def render_clip_window(
    session: Session,
    actor_id: uuid.UUID,
    project: ProductionProject,
    storage: LocalFilesystemStorage,
    start: float,
    end: float,
    settings: Settings | None = None,
    runner: Runner = subprocess.run,
) -> ProductionClip:
    """Render one approved opportunity through the existing FFmpeg command path."""
    settings = settings or get_settings()
    if (
        project.status not in {"SOURCE_READY", "CLIPS_READY"}
        or not project.source_storage_key
        or project.source_duration_seconds is None
    ):
        raise ProductionError(
            "SOURCE_NOT_READY", "an authorized MP4 must be attached before clipping"
        )
    if start < 0 or end <= start or end > project.source_duration_seconds:
        raise ProductionError("INVALID_CLIP_WINDOW", "clip window is outside the downloaded source")
    clip_number = (
        session.scalar(
            select(ProductionClip.clip_number)
            .where(ProductionClip.project_id == project.id)
            .order_by(ProductionClip.clip_number.desc())
            .limit(1)
        )
        or 0
    ) + 1
    source_path = _work_path(project.id, f"opportunity-source-{clip_number}.mp4", settings)
    with storage.open(project.source_storage_key) as source, source_path.open("wb") as target:
        shutil.copyfileobj(source, target)
    try:
        clip = _render_clip_from_source(
            project, source_path, clip_number, start, end, storage, settings, runner
        )
        session.add(clip)
        session.flush()
        if clip.render_status == "SUCCEEDED":
            from app.media_preview.service import ensure_clip_asset

            ensure_clip_asset(session, clip, storage, settings)
        session.add(
            AuditEvent(
                actor_id=actor_id,
                entity_type="production_clip",
                entity_id=clip.id,
                brand_id=clip.brand_id,
                event_name="production.opportunity_clip.generated",
                payload={"start": start, "end": end, "render_status": clip.render_status},
            )
        )
        session.commit()
        return clip
    finally:
        shutil.rmtree(source_path.parent, ignore_errors=True)


def generate_clips(
    session: Session,
    actor_id: uuid.UUID,
    project: ProductionProject,
    storage: LocalFilesystemStorage,
    settings: Settings | None = None,
    runner: Runner = subprocess.run,
) -> list[ProductionClip]:
    settings = settings or get_settings()
    if (
        project.status != "SOURCE_READY"
        or not project.source_storage_key
        or project.source_duration_seconds is None
    ):
        raise ProductionError(
            "SOURCE_NOT_READY", "an authorized MP4 must be attached before clipping"
        )
    if session.scalar(select(ProductionClip).where(ProductionClip.project_id == project.id)):
        raise ProductionError("CLIPS_ALREADY_GENERATED", "clips already exist for this project")
    source_path = _work_path(project.id, "source.mp4", settings)
    with storage.open(project.source_storage_key) as source, source_path.open("wb") as target:
        shutil.copyfileobj(source, target)
    clips: list[ProductionClip] = []
    try:
        for number, (start, end) in enumerate(
            fixed_segments(
                project.source_duration_seconds,
                settings.default_clip_duration_seconds,
                settings.min_clip_duration_seconds,
                settings.clip_overlap_seconds,
            ),
            start=1,
        ):
            clip = _render_clip_from_source(
                project, source_path, number, start, end, storage, settings, runner
            )
            session.add(clip)
            clips.append(clip)
        session.flush()
        from app.media_preview.service import ensure_clip_asset

        for clip in clips:
            if clip.render_status == "SUCCEEDED":
                ensure_clip_asset(session, clip, storage, settings)
        project.status = "CLIPS_READY"
        session.add(
            AuditEvent(
                actor_id=actor_id,
                entity_type="production_project",
                entity_id=project.id,
                brand_id=project.brand_id,
                event_name="production.clips.generated",
                payload={"count": len(clips)},
            )
        )
        session.commit()
        return clips
    finally:
        shutil.rmtree(source_path.parent, ignore_errors=True)


def decide_clip(
    session: Session, actor_id: uuid.UUID, clip: ProductionClip, approved: bool
) -> ProductionClip:
    if clip.render_status != "SUCCEEDED":
        raise ProductionError(
            "CLIP_NOT_RENDERED", "only successfully rendered clips can be reviewed"
        )
    target_status = "APPROVED" if approved else "REJECTED"
    if clip.approval_status == target_status:
        # Component retries and duplicate Discord interactions must not create
        # duplicate queue records or audit events.
        return clip
    clip.approval_status = target_status
    queue = session.scalar(select(PostingQueueItem).where(PostingQueueItem.clip_id == clip.id))
    if approved:
        if queue is None:
            session.add(
                PostingQueueItem(clip_id=clip.id, brand_id=clip.brand_id, caption=clip.caption)
            )
        clip.publication_status = "READY_TO_POST"
    else:
        if queue is not None:
            session.delete(queue)
        clip.publication_status = "NOT_QUEUED"
    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="production_clip",
            entity_id=clip.id,
            brand_id=clip.brand_id,
            event_name="production.clip.approved" if approved else "production.clip.rejected",
        )
    )
    session.commit()
    return clip


def approve_all(session: Session, actor_id: uuid.UUID, project_id: uuid.UUID) -> int:
    clips = list(
        session.scalars(
            select(ProductionClip)
            .where(
                ProductionClip.project_id == project_id, ProductionClip.render_status == "SUCCEEDED"
            )
            .order_by(ProductionClip.clip_number)
        )
    )
    for clip in clips:
        decide_clip(session, actor_id, clip, True)
    return len(clips)


def set_caption(
    session: Session, actor_id: uuid.UUID, clip: ProductionClip, caption: str
) -> ProductionClip:
    clean = " ".join(caption.split())
    if not clean or len(clean) > 2_000:
        raise ProductionError("INVALID_CAPTION", "caption must contain 1 to 2000 characters")
    clip.caption = clean
    queue = session.scalar(select(PostingQueueItem).where(PostingQueueItem.clip_id == clip.id))
    if queue is not None:
        queue.caption = clean
    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="production_clip",
            entity_id=clip.id,
            event_name="production.clip.caption_updated",
        )
    )
    session.commit()
    return clip
