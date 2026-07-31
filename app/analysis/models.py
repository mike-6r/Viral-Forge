"""Persisted, reusable source-analysis results; no clip recommendation policy."""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base
from app.common.types import UUIDTimestampMixin

LEGACY_BRAND_ID = uuid.UUID("4e6768ac-d9bc-4eac-8f30-e73ffc510102")


class AnalysisStatus:
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class VideoAnalysis(UUIDTimestampMixin, Base):
    __tablename__ = "video_analyses"
    __table_args__ = (
        UniqueConstraint("project_id", "analysis_version", name="uq_video_analysis_project_version"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_projects.id"), index=True
    )
    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), default=lambda: LEGACY_BRAND_ID, index=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_sources.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(50), default=AnalysisStatus.QUEUED, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    fps: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    frame_count: Mapped[int | None] = mapped_column(Integer)
    transcript_language: Mapped[str | None] = mapped_column(String(50))
    analysis_version: Mapped[str] = mapped_column(String(100), default="foundation-v1")
    current_stage: Mapped[str | None] = mapped_column(String(100))
    progress_percent: Mapped[float] = mapped_column(Float, default=0.0)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)


class AnalysisSegment(UUIDTimestampMixin, Base):
    __tablename__ = "analysis_segments"

    analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_analyses.id"), index=True
    )
    start_time: Mapped[float] = mapped_column(Float)
    end_time: Mapped[float] = mapped_column(Float)
    segment_type: Mapped[str] = mapped_column(String(100), index=True)
    confidence: Mapped[float | None] = mapped_column(Float)
    score: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)


class TranscriptSegment(UUIDTimestampMixin, Base):
    __tablename__ = "transcript_segments"

    analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_analyses.id"), index=True
    )
    start_time: Mapped[float] = mapped_column(Float)
    end_time: Mapped[float] = mapped_column(Float)
    speaker: Mapped[str | None] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)


class AnalysisEvent(UUIDTimestampMixin, Base):
    __tablename__ = "analysis_events"

    analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_analyses.id"), index=True
    )
    timestamp: Mapped[float] = mapped_column(Float)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    confidence: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
