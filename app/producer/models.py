"""Persisted, explainable AI Producer recommendations.

These records are deliberately advisory.  Their approval never advances a
project, renders a clip, queues a post, or publishes media.
"""

import uuid

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base
from app.common.types import UUIDTimestampMixin


class ProducerRecommendationStatus:
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class ProducerRecommendationType:
    SOURCE_TRUST = "SOURCE_TRUST"
    DOWNLOAD = "DOWNLOAD"
    PROCESS = "PROCESS"
    CLIP_STRATEGY = "CLIP_STRATEGY"
    CLIP_BOUNDARY = "CLIP_BOUNDARY"
    METADATA_VARIANT = "METADATA_VARIANT"
    PUBLISH_READINESS = "PUBLISH_READINESS"


class ProducerRecommendation(UUIDTimestampMixin, Base):
    __tablename__ = "producer_recommendations"

    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), index=True)
    discovered_media_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("discovered_media.id"), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("production_projects.id"), index=True)
    clip_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("production_clips.id"), index=True)
    content_package_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("content_packages.id"), index=True)
    recommendation_type: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(50), default=ProducerRecommendationStatus.PENDING, index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    reasoning: Mapped[str] = mapped_column(Text)
    evidence_json: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    recommendation_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    operator_edit_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    prediction_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    provider_name: Mapped[str] = mapped_column(String(100), default="local_producer")
    model_name: Mapped[str | None] = mapped_column(String(255))
    provider_version: Mapped[str | None] = mapped_column(String(255))
    review_version: Mapped[int] = mapped_column(Integer, default=1)
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    decision_reason: Mapped[str | None] = mapped_column(Text)


class ClipQualityReport(UUIDTimestampMixin, Base):
    __tablename__ = "clip_quality_reports"
    __table_args__ = (UniqueConstraint("clip_id", "report_version", name="uq_clip_quality_report_version"),)

    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("production_projects.id"), index=True)
    clip_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("production_clips.id"), index=True)
    report_version: Mapped[int] = mapped_column(Integer, default=1)
    hook_quality: Mapped[float] = mapped_column(Float)
    pacing_quality: Mapped[float] = mapped_column(Float)
    context_quality: Mapped[float] = mapped_column(Float)
    retention_estimate: Mapped[float] = mapped_column(Float)
    subtitle_quality: Mapped[float] = mapped_column(Float)
    title_quality: Mapped[float] = mapped_column(Float)
    caption_quality: Mapped[float] = mapped_column(Float)
    hashtag_quality: Mapped[float] = mapped_column(Float)
    overall_readiness: Mapped[float] = mapped_column(Float)
    reasoning: Mapped[str] = mapped_column(Text)
    evidence_json: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    recommendations_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    prediction_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    provider_name: Mapped[str] = mapped_column(String(100), default="local_producer")
    model_name: Mapped[str | None] = mapped_column(String(255))
    provider_version: Mapped[str | None] = mapped_column(String(255))


class ProducerOutcomeEvaluation(UUIDTimestampMixin, Base):
    __tablename__ = "producer_outcome_evaluations"
    __table_args__ = (UniqueConstraint("recommendation_id", "snapshot_id", name="uq_producer_outcome_recommendation_snapshot"),)

    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), index=True)
    recommendation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("producer_recommendations.id"), index=True)
    clip_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("production_clips.id"), index=True)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("post_analytics_snapshots.id"), index=True)
    predicted_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    observed_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    evaluation_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
