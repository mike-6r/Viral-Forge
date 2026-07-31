"""Idempotent technical-source analysis; no clip recommendation logic lives here."""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.models import (
    AnalysisEvent,
    AnalysisSegment,
    AnalysisStatus,
    TranscriptSegment,
    VideoAnalysis,
)
from app.audit.models import AuditEvent
from app.common.config import Settings, get_settings
from app.ingestion.storage import LocalFilesystemStorage
from app.production.models import ProductionProject
from app.production.service import ProductionError

Runner = Callable[..., subprocess.CompletedProcess[str]]
logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class TechnicalAnalysis:
    duration_seconds: float
    fps: float
    width: int
    height: int
    frame_count: int | None
    bitrate: int | None
    codec: str
    audio_channels: int | None
    segments: list[tuple[float, float, str, float | None, dict[str, object]]]
    events: list[tuple[float, str, float | None, dict[str, object]]]
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TimedText:
    start_time: float
    end_time: float
    text: str
    speaker: str | None = None
    confidence: float | None = None
    metadata: dict[str, object] = field(default_factory=dict)


class VideoAnalyzer(Protocol):
    def analyze(self, path: Path) -> TechnicalAnalysis: ...


class TranscriptionProvider(Protocol):
    def transcribe(self, path: Path) -> tuple[str | None, list[TimedText]]: ...


class OcrProvider(Protocol):
    def detect(self, path: Path) -> list[tuple[float, str, float | None]]: ...


class VisionProvider(Protocol):
    """Reserved provider boundary for future visual analysis."""

    def analyze_frames(self, path: Path) -> list[tuple[float, str, float | None]]: ...


class ObjectDetectionProvider(Protocol):
    """Reserved provider boundary for future object detection."""

    def detect_objects(self, path: Path) -> list[tuple[float, str, float | None]]: ...


class SummarizationProvider(Protocol):
    """Reserved provider boundary for future LLM summaries."""

    def summarize(self, transcript: list[TimedText]) -> str | None: ...


class FfprobeVideoAnalyzer:
    """Technical metadata and a neutral timeline sampling record only."""

    def __init__(self, settings: Settings | None = None, runner: Runner = subprocess.run) -> None:
        self.settings = settings or get_settings()
        self.runner = runner

    def analyze(self, path: Path) -> TechnicalAnalysis:
        command = [
            self.settings.ffprobe_path,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ]
        try:
            completed = self.runner(
                command,
                capture_output=True,
                text=True,
                timeout=self.settings.analysis_timeout_seconds,
                check=True,
            )
            payload = json.loads(completed.stdout)
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            raise ProductionError("ANALYSIS_PROBE_FAILED", "source could not be analyzed") from error
        video = next(
            (stream for stream in payload.get("streams", []) if stream.get("codec_type") == "video"),
            None,
        )
        audio = next(
            (stream for stream in payload.get("streams", []) if stream.get("codec_type") == "audio"),
            None,
        )
        if not isinstance(video, dict):
            raise ProductionError("ANALYSIS_PROBE_FAILED", "source has no usable video stream")
        duration = float(payload.get("format", {}).get("duration", 0))
        rate = str(video.get("r_frame_rate", "0/1")).split("/")
        fps = float(rate[0]) / float(rate[1]) if len(rate) == 2 and float(rate[1]) else 0
        if duration <= 0 or duration > self.settings.analysis_max_video_duration_seconds:
            raise ProductionError("ANALYSIS_DURATION_INVALID", "source duration is outside analysis limits")
        frame_count = int(video["nb_frames"]) if str(video.get("nb_frames", "")).isdigit() else None
        timeline_metadata: dict[str, object] = {
            "sampling_interval_seconds": self.settings.analysis_frame_sampling_interval_seconds,
            "scene_detection_threshold": self.settings.analysis_scene_detection_threshold,
            "scene_detection": "provider-placeholder",
            "audio_activity": "provider-placeholder",
            "motion_detection": "provider-placeholder",
        }
        segments: list[tuple[float, float, str, float | None, dict[str, object]]] = [
            (0.0, duration, "TIMELINE_SAMPLED", 1.0, timeline_metadata)
        ]
        return TechnicalAnalysis(
            duration_seconds=duration,
            fps=fps,
            width=int(video["width"]),
            height=int(video["height"]),
            frame_count=frame_count,
            bitrate=(
                int(float(video["bit_rate"]))
                if str(video.get("bit_rate", "")).isdigit()
                else None
            ),
            codec=str(video.get("codec_name", "unknown")),
            audio_channels=(
                int(audio["channels"])
                if isinstance(audio, dict) and str(audio.get("channels", "")).isdigit()
                else None
            ),
            segments=segments,
            events=[],
        )


def _bounded(value: float, duration: float) -> float:
    return max(0.0, min(duration, value))


def _merge_ranges(
    ranges: list[tuple[float, float]], gap: float, minimum: float
) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted((start, end) for start, end in ranges if end > start):
        if merged and start - merged[-1][1] <= gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return [(start, end) for start, end in merged if end - start >= minimum]


def _complement_ranges(
    ranges: list[tuple[float, float]], duration: float, minimum: float
) -> list[tuple[float, float]]:
    speech: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in ranges:
        if start - cursor >= minimum:
            speech.append((cursor, start))
        cursor = max(cursor, end)
    if duration - cursor >= minimum:
        speech.append((cursor, duration))
    return speech


def _metadata_frames(output: str) -> list[tuple[float, dict[str, float]]]:
    """Parse FFmpeg ametadata output without retaining samples or frames."""
    frames: list[tuple[float, dict[str, float]]] = []
    timestamp: float | None = None
    values: dict[str, float] = {}
    for line in output.splitlines():
        match = re.search(r"pts_time:([-0-9.]+)", line)
        if match:
            if timestamp is not None:
                frames.append((timestamp, values))
            timestamp, values = float(match.group(1)), {}
            continue
        value_match = re.search(r"(lavfi\.[A-Za-z0-9_.]+)=(-?(?:inf|[0-9.]+))", line)
        if value_match and timestamp is not None and value_match.group(2) not in {"inf", "-inf"}:
            values[value_match.group(1)] = float(value_match.group(2))
    if timestamp is not None:
        frames.append((timestamp, values))
    return frames


def _provider_metadata(name: str) -> dict[str, object]:
    return {"provider": name}


class LocalMediaAnalyzer:
    """Bounded FFmpeg media signals; it persists facts, never frames or audio samples."""

    def __init__(self, settings: Settings | None = None, runner: Runner = subprocess.run) -> None:
        self.settings = settings or get_settings()
        self.runner = runner

    def _run(self, command: list[str], timeout: int) -> str:
        completed = self.runner(command, capture_output=True, text=True, timeout=timeout, check=True)
        return f"{completed.stdout}\n{completed.stderr}"

    def _silence(self, path: Path, duration: float) -> tuple[list[tuple[float, float]], list[str]]:
        command = [
            self.settings.ffmpeg_path,
            "-hide_banner",
            "-nostdin",
            "-i",
            str(path),
            "-af",
            f"silencedetect=noise={self.settings.analysis_silence_noise_threshold}:d={self.settings.analysis_min_silence_duration_seconds}",
            "-f",
            "null",
            "-",
        ]
        try:
            output = self._run(command, self.settings.analysis_timeout_seconds)
        except (OSError, subprocess.SubprocessError):
            return [], ["silence analysis unavailable or source has no audio"]
        starts = [float(value) for value in re.findall(r"silence_start: ([0-9.]+)", output)]
        ends = [float(value) for value in re.findall(r"silence_end: ([0-9.]+)", output)]
        ranges = [
            (_bounded(start, duration), _bounded(ends[index] if index < len(ends) else duration, duration))
            for index, start in enumerate(starts)
        ]
        return _merge_ranges(
            ranges,
            self.settings.analysis_merge_gap_seconds,
            self.settings.analysis_min_silence_duration_seconds,
        ), []

    def _scene_cuts(self, path: Path, duration: float) -> tuple[list[float], list[str]]:
        command = [
            self.settings.ffmpeg_path,
            "-hide_banner",
            "-nostdin",
            "-i",
            str(path),
            "-vf",
            f"select=gt(scene\\,{self.settings.analysis_scene_detection_threshold}),showinfo",
            "-an",
            "-frames:v",
            str(self.settings.analysis_scene_max_sampled_frames),
            "-f",
            "null",
            "-",
        ]
        try:
            output = self._run(command, self.settings.analysis_timeout_seconds)
        except (OSError, subprocess.SubprocessError):
            return [], ["scene detection unavailable"]
        cuts = [
            _bounded(float(value), duration)
            for value in re.findall(r"pts_time:([-0-9.]+)", output)
        ]
        result: list[float] = []
        for cut in sorted(set(cuts)):
            if cut > 0 and (not result or cut - result[-1] >= self.settings.analysis_scene_min_duration_seconds):
                result.append(cut)
        return result, []

    def _audio(self, path: Path, duration: float) -> tuple[list[tuple[float, float, str, float]], list[tuple[float, str, float]], list[str]]:
        samples = max(1, int(8_000 * self.settings.analysis_audio_sample_interval_seconds))
        command = [
            self.settings.ffmpeg_path,
            "-hide_banner",
            "-nostdin",
            "-i",
            str(path),
            "-vn",
            "-af",
            f"aresample=8000,asetnsamples=n={samples}:p=1,astats=metadata=1:reset=1,ametadata=print",
            "-f",
            "null",
            "-",
        ]
        try:
            frames = _metadata_frames(self._run(command, self.settings.analysis_timeout_seconds))
        except (OSError, subprocess.SubprocessError):
            return [], [], ["audio timeline unavailable or source has no audio"]
        intervals: list[tuple[float, float, str, float]] = []
        events: list[tuple[float, str, float]] = []
        for timestamp, values in frames:
            rms = values.get("lavfi.astats.Overall.RMS_level")
            peak = values.get("lavfi.astats.Overall.Peak_level")
            end = _bounded(timestamp + self.settings.analysis_audio_sample_interval_seconds, duration)
            if rms is not None and rms >= self.settings.analysis_audio_loudness_threshold_db:
                intervals.append((timestamp, end, "LOUD_AUDIO", min(1.0, max(0.0, (rms + 60) / 60))))
            elif rms is not None:
                intervals.append((timestamp, end, "QUIET_AUDIO", min(1.0, max(0.0, (-rms) / 100))))
            if peak is not None and peak >= self.settings.analysis_audio_peak_threshold_db:
                events.append((timestamp, "AUDIO_PEAK", min(1.0, max(0.0, (peak + 60) / 60))))
        return intervals, events, []

    def _motion(self, path: Path, duration: float) -> tuple[list[tuple[float, float, str, float]], list[tuple[float, str, float]], list[str]]:
        interval = self.settings.analysis_motion_sample_interval_seconds
        command = [
            self.settings.ffmpeg_path,
            "-hide_banner",
            "-nostdin",
            "-i",
            str(path),
            "-vf",
            f"fps=1/{interval},scale={self.settings.analysis_motion_sample_width}:-2,format=gray,tblend=all_mode=difference,signalstats,metadata=print",
            "-an",
            "-frames:v",
            str(self.settings.analysis_motion_max_samples),
            "-f",
            "null",
            "-",
        ]
        try:
            frames = _metadata_frames(self._run(command, self.settings.analysis_timeout_seconds))
        except (OSError, subprocess.SubprocessError):
            return [], [], ["motion analysis unavailable"]
        segments: list[tuple[float, float, str, float]] = []
        events: list[tuple[float, str, float]] = []
        for timestamp, values in frames:
            score = values.get("lavfi.signalstats.YAVG", 0.0) / 255.0
            end = _bounded(timestamp + interval, duration)
            level = "LOW" if score < self.settings.analysis_motion_low_threshold else "MEDIUM"
            if score >= self.settings.analysis_motion_high_threshold:
                level = "HIGH"
                events.append((timestamp, "MOTION_SPIKE", score))
            segments.append((timestamp, end, "MOTION", score if level != "LOW" else score / 2))
        return segments, events, []

    def analyze(self, path: Path) -> TechnicalAnalysis:
        technical = FfprobeVideoAnalyzer(self.settings, self.runner).analyze(path)
        duration = technical.duration_seconds
        warnings: list[str] = []
        silence, silence_warnings = self._silence(path, duration)
        cuts, scene_warnings = self._scene_cuts(path, duration)
        audio, audio_events, audio_warnings = self._audio(path, duration)
        motion, motion_events, motion_warnings = self._motion(path, duration)
        warnings.extend(silence_warnings + scene_warnings + audio_warnings + motion_warnings)
        scenes: list[tuple[float, float, str, float | None, dict[str, object]]] = []
        boundaries = [0.0, *cuts, duration]
        for start, end in zip(boundaries, boundaries[1:], strict=False):
            if end - start >= self.settings.analysis_scene_min_duration_seconds:
                scenes.append((start, end, "SCENE", 1.0, _provider_metadata("ffmpeg_scene")))
        segments: list[tuple[float, float, str, float | None, dict[str, object]]] = [
            (0.0, duration, "TIMELINE_SAMPLED", 1.0, _provider_metadata("local-media-v1")),
            *[(start, end, "SILENCE", 1.0, _provider_metadata("ffmpeg_silencedetect")) for start, end in silence],
            *[(start, end, "SPEECH", 1.0, _provider_metadata("silence-complement")) for start, end in _complement_ranges(silence, duration, self.settings.analysis_min_speech_duration_seconds)],
            *scenes,
            *[(start, end, kind, score, _provider_metadata("ffmpeg_astats")) for start, end, kind, score in audio],
            *[(start, end, kind, score, _provider_metadata("ffmpeg_signalstats")) for start, end, kind, score in motion],
        ]
        events: list[tuple[float, str, float | None, dict[str, object]]] = [
            (cut, "SHOT_CHANGE", None, _provider_metadata("ffmpeg_scene")) for cut in cuts
        ]
        events.extend((start, "LONG_SILENCE", None, {"duration_seconds": end - start}) for start, end in silence if end - start >= self.settings.analysis_long_silence_seconds)
        events.extend((timestamp, kind, score, _provider_metadata("ffmpeg_astats")) for timestamp, kind, score in audio_events)
        events.extend((timestamp, kind, score, _provider_metadata("ffmpeg_signalstats")) for timestamp, kind, score in motion_events)
        return TechnicalAnalysis(
            duration_seconds=duration,
            fps=technical.fps,
            width=technical.width,
            height=technical.height,
            frame_count=technical.frame_count,
            bitrate=technical.bitrate,
            codec=technical.codec,
            audio_channels=technical.audio_channels,
            segments=segments,
            events=events,
            warnings=warnings,
        )


class MockTranscriptionProvider:
    """Safe default for unavailable transcription; it intentionally returns no transcript."""

    def transcribe(self, path: Path) -> tuple[str | None, list[TimedText]]:
        return None, []


class FasterWhisperTranscriptionProvider:
    """Local, lazy faster-whisper integration; no model is loaded at API startup."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.metadata: dict[str, object] = {
            "provider": "faster-whisper",
            "model": self.settings.analysis_transcript_model,
            "device": self.settings.analysis_transcript_device,
            "compute_type": self.settings.analysis_transcript_compute_type,
        }

    def transcribe(self, path: Path) -> tuple[str | None, list[TimedText]]:
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-untyped]
        except ImportError as error:
            raise ProductionError(
                "TRANSCRIPTION_PROVIDER_UNAVAILABLE", "faster-whisper is not installed"
            ) from error
        model = WhisperModel(
            self.settings.analysis_transcript_model,
            device=self.settings.analysis_transcript_device,
            compute_type=self.settings.analysis_transcript_compute_type,
            download_root=self.settings.analysis_model_cache_root,
        )
        segments, info = model.transcribe(
            str(path),
            language=self.settings.analysis_transcript_language,
            beam_size=self.settings.analysis_transcript_beam_size,
            word_timestamps=self.settings.analysis_transcript_word_timestamps,
            vad_filter=self.settings.analysis_transcript_vad_enabled,
        )
        language = getattr(info, "language", None)
        probability = getattr(info, "language_probability", None)
        if isinstance(probability, (float, int)):
            self.metadata["language_confidence"] = float(probability)
        timed: list[TimedText] = []
        for segment in segments:
            start, end = float(segment.start), float(segment.end)
            if end <= start:
                continue
            word_metadata: dict[str, object] = {}
            if self.settings.analysis_transcript_word_timestamps:
                words = [
                    {
                        "start": round(float(word.start), 3),
                        "end": round(float(word.end), 3),
                        "word": str(word.word)[:100],
                        "probability": round(float(word.probability), 4)
                        if getattr(word, "probability", None) is not None
                        else None,
                    }
                    for word in list(getattr(segment, "words", []) or [])[:200]
                ]
                if words:
                    word_metadata["words"] = words
            average_log_probability = getattr(segment, "avg_logprob", None)
            confidence = (
                min(1.0, max(0.0, math.exp(float(average_log_probability))))
                if isinstance(average_log_probability, (float, int))
                else None
            )
            timed.append(
                TimedText(
                    start_time=start,
                    end_time=end,
                    text=str(segment.text).strip()[:10_000],
                    confidence=confidence,
                    metadata=word_metadata,
                )
            )
        self.metadata["segment_count"] = len(timed)
        return str(language) if language else None, timed


class MockOcrProvider:
    """Safe default for unavailable OCR; it intentionally returns no events."""

    def detect(self, path: Path) -> list[tuple[float, str, float | None]]:
        return []


class TesseractOcrProvider:
    """Optional bounded OCR through local Tesseract; text is never treated as a watermark."""

    def __init__(self, settings: Settings | None = None, runner: Runner = subprocess.run) -> None:
        self.settings = settings or get_settings()
        self.runner = runner

    def detect(self, path: Path) -> list[tuple[float, str, float | None]]:
        probe = FfprobeVideoAnalyzer(self.settings, self.runner).analyze(path)
        count = min(self.settings.analysis_ocr_sample_count, max(1, int(probe.duration_seconds)))
        timestamps = [probe.duration_seconds * index / count for index in range(count)]
        results: list[tuple[float, str, float | None]] = []
        previous = ""
        for timestamp in timestamps:
            image = subprocess.run(
                [
                    self.settings.ffmpeg_path,
                    "-hide_banner",
                    "-nostdin",
                    "-ss",
                    f"{timestamp:.3f}",
                    "-i",
                    str(path),
                    "-frames:v",
                    "1",
                    "-f",
                    "image2pipe",
                    "-vcodec",
                    "png",
                    "-",
                ],
                capture_output=True,
                timeout=self.settings.analysis_timeout_seconds,
                check=True,
            )
            recognized = subprocess.run(
                ["tesseract", "stdin", "stdout", "--psm", "6"],
                input=image.stdout,
                capture_output=True,
                timeout=self.settings.analysis_timeout_seconds,
                check=True,
            )
            text = " ".join(recognized.stdout.decode(errors="replace").split())[:1_000]
            if text and text != previous:
                results.append((timestamp, text, None))
                previous = text
        return results


def video_analyzer_for(settings: Settings) -> VideoAnalyzer:
    if settings.analysis_video_provider in {"ffprobe", "local_ffmpeg"}:
        return LocalMediaAnalyzer(settings)
    raise ProductionError("ANALYSIS_PROVIDER_INVALID", "configured video provider is unavailable")


def transcription_provider_for(settings: Settings) -> TranscriptionProvider:
    if settings.analysis_transcript_provider == "faster_whisper":
        return FasterWhisperTranscriptionProvider(settings)
    if settings.analysis_transcript_provider == "mock":
        return MockTranscriptionProvider()
    raise ProductionError("TRANSCRIPTION_PROVIDER_INVALID", "configured transcription provider is unavailable")


def ocr_provider_for(settings: Settings) -> OcrProvider:
    if not settings.analysis_ocr_enabled:
        return MockOcrProvider()
    if settings.analysis_ocr_provider == "tesseract":
        return TesseractOcrProvider(settings)
    if settings.analysis_ocr_provider == "mock":
        return MockOcrProvider()
    raise ProductionError("OCR_PROVIDER_INVALID", "configured OCR provider is unavailable")


def normalize_timeline(
    duration: float,
    segments: list[tuple[float, float, str, float | None, dict[str, object]]],
    events: list[tuple[float, str, float | None, dict[str, object]]],
    maximum_events: int,
) -> tuple[
    list[tuple[float, float, str, float | None, dict[str, object]]],
    list[tuple[float, str, float | None, dict[str, object]]],
    list[str],
]:
    """Clamp, deduplicate, and deterministically cap timeline facts."""
    warnings: list[str] = []
    normalized_segments: list[tuple[float, float, str, float | None, dict[str, object]]] = []
    seen_segments: set[tuple[float, float, str]] = set()
    for start, end, kind, confidence, metadata in segments:
        bounded_start, bounded_end = _bounded(start, duration), _bounded(end, duration)
        key = (round(bounded_start, 3), round(bounded_end, 3), kind)
        if bounded_end <= bounded_start or key in seen_segments:
            continue
        seen_segments.add(key)
        normalized_segments.append((bounded_start, bounded_end, kind, confidence, metadata))
    normalized_events: list[tuple[float, str, float | None, dict[str, object]]] = []
    seen_events: set[tuple[float, str]] = set()
    for timestamp, kind, confidence, metadata in events:
        bounded_timestamp = _bounded(timestamp, duration)
        event_key = (round(bounded_timestamp, 3), kind)
        if event_key in seen_events:
            continue
        seen_events.add(event_key)
        normalized_events.append((bounded_timestamp, kind, confidence, metadata))
    normalized_segments.sort(key=lambda item: (item[0], item[1], item[2]))
    normalized_events.sort(key=lambda item: (item[0], item[1]))
    if len(normalized_events) > maximum_events:
        normalized_events = normalized_events[:maximum_events]
        warnings.append("timeline events were truncated to the configured maximum")
    return normalized_segments, normalized_events, warnings


def _set_stage(session: Session, analysis: VideoAnalysis, stage: str, progress: float) -> None:
    analysis.current_stage = stage
    analysis.progress_percent = progress
    session.commit()


def _cancelled(session: Session, analysis: VideoAnalysis) -> bool:
    session.refresh(analysis)
    return analysis.status == AnalysisStatus.CANCELLED


def request_analysis(
    session: Session,
    actor_id: uuid.UUID,
    project: ProductionProject,
    rerun: bool = False,
    analysis_version: str | None = None,
) -> VideoAnalysis:
    """Create or return a versioned analysis without overwriting foundation records."""
    settings = get_settings()
    if not settings.analysis_enabled:
        raise ProductionError("ANALYSIS_DISABLED", "analysis is disabled by configuration")
    version = analysis_version or settings.analysis_version
    existing = session.scalar(
        select(VideoAnalysis).where(
            VideoAnalysis.project_id == project.id,
            VideoAnalysis.analysis_version == version,
        )
    )
    if existing is not None:
        if existing.status == AnalysisStatus.RUNNING and rerun:
            raise ProductionError("ANALYSIS_ALREADY_RUNNING", "analysis is already running")
        if not rerun:
            return existing
    if not project.source_storage_key:
        raise ProductionError("ANALYSIS_SOURCE_NOT_READY", "download the source before analysis")
    if existing is None:
        existing = VideoAnalysis(
            project_id=project.id,
            brand_id=project.brand_id,
            source_id=project.selected_source_id,
            status=AnalysisStatus.QUEUED,
            analysis_version=version,
            current_stage="QUEUED",
            progress_percent=0.0,
        )
        session.add(existing)
        session.flush()
    else:
        existing.status = AnalysisStatus.QUEUED
        existing.started_at = None
        existing.completed_at = None
        existing.current_stage = "QUEUED"
        existing.progress_percent = 0.0
        existing.metadata_json = {**existing.metadata_json, "explicit_rerun": True}
    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="video_analysis",
            entity_id=existing.id,
            brand_id=existing.brand_id,
            event_name="analysis.queued",
        )
    )
    session.commit()
    return existing


def cancel_analysis(session: Session, actor_id: uuid.UUID, analysis: VideoAnalysis) -> VideoAnalysis:
    """Request cancellation; the worker observes it between bounded provider stages."""
    if analysis.status == AnalysisStatus.COMPLETED:
        raise ProductionError("ANALYSIS_ALREADY_COMPLETED", "completed analysis cannot be cancelled")
    if analysis.status != AnalysisStatus.CANCELLED:
        analysis.status = AnalysisStatus.CANCELLED
        analysis.completed_at = datetime.now(UTC)
        analysis.current_stage = "CANCELLED"
        session.add(
            AuditEvent(
                actor_id=actor_id,
                entity_type="video_analysis",
                entity_id=analysis.id,
                event_name="analysis.cancelled",
            )
        )
        session.commit()
    return analysis


def execute_analysis(
    session: Session,
    actor_id: uuid.UUID,
    analysis: VideoAnalysis,
    storage: LocalFilesystemStorage,
    analyzer: VideoAnalyzer | None = None,
    transcription: TranscriptionProvider | None = None,
    ocr: OcrProvider | None = None,
    settings: Settings | None = None,
) -> VideoAnalysis:
    """Run bounded local stages and persist normalized facts without logging content."""
    settings = settings or get_settings()
    project = session.get(ProductionProject, analysis.project_id)
    if project is None or not project.source_storage_key:
        raise ProductionError("ANALYSIS_SOURCE_NOT_READY", "downloaded source is unavailable")
    if analysis.status in {
        AnalysisStatus.COMPLETED,
        AnalysisStatus.RUNNING,
        AnalysisStatus.CANCELLED,
    }:
        return analysis
    source_path = Path(settings.video_work_root).resolve() / analysis.id.hex / "analysis-source.mp4"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    analysis.status = AnalysisStatus.RUNNING
    analysis.started_at = datetime.now(UTC)
    analysis.current_stage = "METADATA"
    analysis.progress_percent = 5.0
    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="video_analysis",
            entity_id=analysis.id,
            event_name="analysis.started",
        )
    )
    session.commit()
    logger.info("analysis_started", analysis_id=str(analysis.id), project_id=str(project.id))
    started_monotonic = time.monotonic()
    try:
        with storage.open(project.source_storage_key) as source, source_path.open("wb") as target:
            shutil.copyfileobj(source, target)
        selected_analyzer = analyzer or video_analyzer_for(settings)
        result = selected_analyzer.analyze(source_path)
        if _cancelled(session, analysis):
            return analysis
        analysis.duration_seconds = result.duration_seconds
        analysis.fps = result.fps
        analysis.width = result.width
        analysis.height = result.height
        analysis.frame_count = result.frame_count
        analysis.metadata_json = {
            "bitrate": result.bitrate,
            "codec": result.codec,
            "audio_channels": result.audio_channels,
            "provider": type(selected_analyzer).__name__,
            "analysis_version": analysis.analysis_version,
            "warnings": result.warnings,
        }
        _set_stage(session, analysis, "TIMELINE", 35.0)
        segments, events, timeline_warnings = normalize_timeline(
            result.duration_seconds,
            result.segments,
            result.events,
            settings.analysis_timeline_max_events,
        )
        analysis.metadata_json = {
            **analysis.metadata_json,
            "warnings": [*result.warnings, *timeline_warnings],
        }
        session.query(AnalysisSegment).filter(AnalysisSegment.analysis_id == analysis.id).delete()
        session.query(AnalysisEvent).filter(AnalysisEvent.analysis_id == analysis.id).delete()
        session.query(TranscriptSegment).filter(TranscriptSegment.analysis_id == analysis.id).delete()
        session.add_all(
            [
                AnalysisSegment(
                    analysis_id=analysis.id,
                    start_time=start,
                    end_time=end,
                    segment_type=kind,
                    confidence=confidence,
                    score=None,
                    metadata_json=metadata,
                )
                for start, end, kind, confidence, metadata in segments
            ]
        )
        session.add_all(
            [
                AnalysisEvent(
                    analysis_id=analysis.id,
                    timestamp=timestamp,
                    event_type=kind,
                    confidence=confidence,
                    metadata_json=metadata,
                )
                for timestamp, kind, confidence, metadata in events
            ]
        )
        session.commit()
        if _cancelled(session, analysis):
            return analysis
        try:
            _set_stage(session, analysis, "TRANSCRIPTION", 50.0)
            selected_transcription = transcription or transcription_provider_for(settings)
            language, transcript = selected_transcription.transcribe(source_path)
            analysis.transcript_language = language
            provider_metadata = getattr(selected_transcription, "metadata", {})
            if isinstance(provider_metadata, dict):
                analysis.metadata_json = {
                    **analysis.metadata_json,
                    "transcription": provider_metadata,
                }
            session.add_all(
                [
                    TranscriptSegment(
                        analysis_id=analysis.id,
                        start_time=item.start_time,
                        end_time=item.end_time,
                        speaker=item.speaker,
                        text=item.text,
                        confidence=item.confidence,
                        metadata_json=item.metadata,
                    )
                    for item in transcript
                ]
            )
            session.commit()
        except Exception as error:
            analysis.metadata_json = {**analysis.metadata_json, "transcription_error": type(error).__name__}
            logger.warning("analysis_transcription_failed", analysis_id=str(analysis.id), error=type(error).__name__)
            session.commit()
        if _cancelled(session, analysis):
            return analysis
        try:
            _set_stage(session, analysis, "OCR", 75.0)
            session.add_all(
                [
                    AnalysisEvent(
                        analysis_id=analysis.id,
                        timestamp=timestamp,
                        event_type="TEXT_DETECTED",
                        confidence=confidence,
                        metadata_json={"text": text},
                    )
                for timestamp, text, confidence in (ocr or ocr_provider_for(settings)).detect(source_path)
            ]
            )
            session.commit()
        except Exception as error:
            analysis.metadata_json = {**analysis.metadata_json, "ocr_error": type(error).__name__}
            logger.warning("analysis_ocr_failed", analysis_id=str(analysis.id), error=type(error).__name__)
            session.commit()
        if _cancelled(session, analysis):
            return analysis
        _set_stage(session, analysis, "COMPLETED", 100.0)
        analysis.status = AnalysisStatus.COMPLETED
        analysis.completed_at = datetime.now(UTC)
        session.add(
            AuditEvent(
                actor_id=actor_id,
                entity_type="video_analysis",
                entity_id=analysis.id,
                event_name="analysis.completed",
                payload={"duration_seconds": analysis.duration_seconds},
            )
        )
        session.commit()
        logger.info(
            "analysis_completed",
            analysis_id=str(analysis.id),
            duration_seconds=analysis.duration_seconds,
            processing_seconds=round(time.monotonic() - started_monotonic, 3),
        )
        return analysis
    except Exception:
        session.rollback()
        persisted = session.get(VideoAnalysis, analysis.id)
        assert persisted is not None
        persisted.status = AnalysisStatus.FAILED
        persisted.completed_at = datetime.now(UTC)
        session.add(
            AuditEvent(
                actor_id=actor_id,
                entity_type="video_analysis",
                entity_id=persisted.id,
                event_name="analysis.failed",
            )
        )
        session.commit()
        logger.warning("analysis_failed", analysis_id=str(analysis.id))
        raise
    finally:
        shutil.rmtree(source_path.parent, ignore_errors=True)
