"""Evidence-bound content packages for rendered clips, with no publishing side effects."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol, cast

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.models import AnalysisEvent, AnalysisStatus, TranscriptSegment, VideoAnalysis
from app.audit.models import AuditEvent
from app.common.config import Settings, get_settings
from app.content_packages.models import (
    ContentPackage,
    ContentPackageStatus,
    ContentPackageVersion,
)
from app.opportunities.models import ClipOpportunity, OpportunityReason
from app.production.models import ProductionClip, ProductionProject, ProductionSource
from app.production.service import ProductionError


@dataclass(frozen=True)
class ContentPackageContext:
    clip: ProductionClip
    project: ProductionProject
    source: ProductionSource | None
    analysis: VideoAnalysis | None
    verified_facts: list[str]
    transcript_statements: list[str]
    opportunity_reasons: list[str]
    event_types: list[str]


@dataclass(frozen=True)
class ContentPackageDraft:
    provider_name: str
    model_name: str | None
    provider_version: str | None
    language: str
    content_category: str
    confidence: float
    explanation: str
    fields: dict[str, object]
    verified_facts: list[str]
    transcript_statements: list[str]
    uncertainty: list[str]
    warnings: list[str]


class ContentPackageProvider(Protocol):
    def generate(self, context: ContentPackageContext) -> ContentPackageDraft: ...


def _compact(value: str, limit: int = 280) -> str:
    normalized = " ".join(value.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 1].rstrip() + "…"


def _source_attribution(context: ContentPackageContext) -> str:
    source_name = (
        context.source.uploader_name
        if context.source is not None and context.source.uploader_name
        else context.project.source_channel
    )
    title = context.project.source_title or (
        context.source.video_title if context.source is not None else None
    )
    if source_name and title:
        return f"Source: {source_name} — {title}"
    if source_name:
        return f"Source: {source_name}"
    if title:
        return f"Source: {title}"
    return "Source: original persisted project source"


_SENSITIVE_SOURCE_TITLE_TERMS = (
    "shooting",
    "stabbing",
    "homicide",
    "gunfire",
)


def _content_warnings(context: ContentPackageContext) -> list[str]:
    """Return conservative, evidence-labelled review warnings without classifying events."""
    warnings = list(context.source.warnings if context.source is not None else [])
    source_titles = [context.project.source_title]
    if context.source is not None:
        source_titles.append(context.source.video_title)
    for title in source_titles:
        normalized = (title or "").casefold()
        term = next((value for value in _SENSITIVE_SOURCE_TITLE_TERMS if value in normalized), None)
        if term is not None:
            warnings.append(
                f"Potentially sensitive content: persisted source title contains '{term}'; review full context before use."
            )
            break
    return list(dict.fromkeys(warnings))


class LocalTemplateContentPackageProvider:
    """Safe local defaults. Every statement is a label, source fact, or direct transcript text."""

    def generate(self, context: ContentPackageContext) -> ContentPackageDraft:
        attribution = _source_attribution(context)
        title = context.project.source_title or "source clip"
        transcript_excerpt = context.transcript_statements[0] if context.transcript_statements else ""
        transcript_label = f'“{_compact(transcript_excerpt, 140)}”' if transcript_excerpt else title
        primary_hook = f"From the source: {transcript_label}"
        hashtags = ["#SourceClip", "#Shorts"]
        warnings = _content_warnings(context)
        if not context.transcript_statements:
            warnings.append("No transcript statement overlaps this rendered clip; review context manually.")
        uncertainty = [
            "Generated wording is limited to persisted source metadata, transcript statements, opportunity reasons, and analysis events.",
            "Review the complete source context before using or publishing this package.",
        ]
        language = context.analysis.transcript_language if context.analysis and context.analysis.transcript_language else "und"
        confidence = 0.7 if context.transcript_statements else 0.35
        if context.analysis is None:
            confidence = min(confidence, 0.3)
        description = f"{attribution}. Rendered clip from {context.clip.start_seconds:.1f}s to {context.clip.end_seconds:.1f}s."
        fields: dict[str, object] = {
            "primary_hook": primary_hook,
            "alternate_hooks": [f"Source segment: {title}", f"Watch this source clip: {transcript_label}"],
            "neutral_factual_summary": description + (f" Transcript statement: {transcript_label}." if transcript_excerpt else ""),
            "youtube_shorts_title": _compact(title, 100),
            "tiktok_caption": f"{primary_hook}\n{attribution}",
            "instagram_caption": f"{primary_hook}\n\n{attribution}",
            "facebook_caption": f"{primary_hook}\n{attribution}",
            "x_post": _compact(f"{primary_hook} {attribution}", 280),
            "description": description,
            "hashtags": hashtags,
            "seo_keywords": [value for value in [title, context.project.source_channel] if value],
            "thumbnail_text": _compact(transcript_excerpt or title, 48),
            "content_category": "SOURCE_CLIP",
            "language": language,
            "sensitive_content_warnings": warnings,
            "source_attribution_text": attribution,
            "generated_marketing_language": {
                "primary_hook": primary_hook,
                "platform_captions": "Platform caption templates are editable suggestions, not verified facts.",
            },
        }
        explanation_parts = ["Local template provider used persisted clip and source metadata."]
        if context.transcript_statements:
            explanation_parts.append("Transcript-derived statements were available.")
        if context.opportunity_reasons:
            explanation_parts.append("Opportunity reasons were included as supporting context.")
        return ContentPackageDraft(
            provider_name="local_template",
            model_name="deterministic-template",
            provider_version="v2",
            language=language,
            content_category="SOURCE_CLIP",
            confidence=confidence,
            explanation=" ".join(explanation_parts),
            fields=fields,
            verified_facts=context.verified_facts,
            transcript_statements=context.transcript_statements,
            uncertainty=uncertainty,
            warnings=warnings,
        )


class ExternalHttpContentPackageProvider:
    """Optional provider; it is unavailable unless both endpoint and credential are configured."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def generate(self, context: ContentPackageContext) -> ContentPackageDraft:
        if not self.settings.content_package_external_endpoint or not self.settings.content_package_external_api_key:
            raise ProductionError("CONTENT_PACKAGE_PROVIDER_UNAVAILABLE", "external provider is not fully configured")
        payload = {
            "verified_facts": context.verified_facts,
            "transcript_statements": context.transcript_statements,
            "opportunity_reasons": context.opportunity_reasons,
            "analysis_events": context.event_types,
            "instruction": "Return only JSON suggestions grounded in the supplied evidence. Do not invent facts.",
        }
        response = httpx.post(
            self.settings.content_package_external_endpoint,
            json=payload,
            headers={"Authorization": f"Bearer {self.settings.content_package_external_api_key}"},
            timeout=self.settings.content_package_timeout_seconds,
        )
        response.raise_for_status()
        body = cast(dict[str, object], response.json())
        fields = cast(dict[str, object], body.get("fields", {}))
        if not fields:
            raise ProductionError("CONTENT_PACKAGE_PROVIDER_INVALID", "external provider returned no editable fields")
        return ContentPackageDraft(
            provider_name="external_http",
            model_name=cast(str | None, body.get("model_name")),
            provider_version=cast(str | None, body.get("provider_version")),
            language=cast(str, body.get("language", "und")),
            content_category=cast(str, body.get("content_category", "SOURCE_CLIP")),
            confidence=float(cast(float | str, body.get("confidence", 0.0))),
            explanation=cast(str, body.get("explanation", "External evidence-bound response.")),
            fields=fields,
            verified_facts=context.verified_facts,
            transcript_statements=context.transcript_statements,
            uncertainty=list(cast(list[str], body.get("uncertainty", []))) + ["External output requires human review."],
            warnings=list(cast(list[str], body.get("warnings", []))),
        )


def content_package_provider_for(settings: Settings) -> ContentPackageProvider:
    if settings.content_package_provider in {"mock", "local_template"}:
        return LocalTemplateContentPackageProvider()
    if settings.content_package_provider == "external_http":
        return ExternalHttpContentPackageProvider(settings)
    raise ProductionError("CONTENT_PACKAGE_PROVIDER_INVALID", "configured content-package provider is unavailable")


def _context_for(session: Session, clip: ProductionClip, settings: Settings) -> ContentPackageContext:
    project = session.get(ProductionProject, clip.project_id)
    if project is None:
        raise ProductionError("PROJECT_NOT_FOUND", "clip project was not found")
    source = session.get(ProductionSource, project.selected_source_id) if project.selected_source_id else None
    analysis = session.scalar(
        select(VideoAnalysis)
        .where(VideoAnalysis.project_id == project.id, VideoAnalysis.status == AnalysisStatus.COMPLETED)
        .order_by(VideoAnalysis.created_at.desc())
    )
    verified = [
        f"Source URL: {project.source_url}",
        f"Rendered clip window: {clip.start_seconds:.1f}s–{clip.end_seconds:.1f}s",
    ]
    if project.source_title:
        verified.append(f"Source title: {project.source_title}")
    if project.source_channel:
        verified.append(f"Source channel: {project.source_channel}")
    if source is not None:
        if source.uploader_name:
            verified.append(f"Source uploader: {source.uploader_name}")
        if source.source_url != project.source_url:
            verified.append(f"Selected source URL: {source.source_url}")
    transcript: list[str] = []
    events: list[str] = []
    reasons: list[str] = []
    if analysis is not None:
        rows = session.scalars(
            select(TranscriptSegment)
            .where(
                TranscriptSegment.analysis_id == analysis.id,
                TranscriptSegment.end_time >= clip.start_seconds,
                TranscriptSegment.start_time <= clip.end_seconds,
            )
            .order_by(TranscriptSegment.start_time)
        )
        transcript = [_compact(row.text, settings.content_package_max_transcript_chars) for row in rows if row.text.strip()]
        event_rows = session.scalars(
            select(AnalysisEvent)
            .where(
                AnalysisEvent.analysis_id == analysis.id,
                AnalysisEvent.timestamp >= clip.start_seconds,
                AnalysisEvent.timestamp <= clip.end_seconds,
            )
            .order_by(AnalysisEvent.timestamp)
        )
        events = [row.event_type for row in event_rows]
        opportunity = session.scalar(select(ClipOpportunity).where(ClipOpportunity.generated_clip_id == clip.id))
        if opportunity is not None:
            reason_rows = session.scalars(
                select(OpportunityReason).where(OpportunityReason.opportunity_id == opportunity.id)
            )
            reasons = [row.reason_type for row in reason_rows]
    return ContentPackageContext(clip, project, source, analysis, verified, transcript[:8], reasons[:8], events[:12])


def _snapshot(package: ContentPackage) -> dict[str, object]:
    return {
        "generation_version": package.generation_version,
        "review_version": package.review_version,
        "status": package.status,
        "fields": package.fields_json,
        "verified_facts": package.verified_facts_json,
        "transcript_statements": package.transcript_statements_json,
        "uncertainty": package.uncertainty_json,
        "warnings": package.warnings_json,
    }


def _record_version(
    session: Session, package: ContentPackage, actor_id: uuid.UUID | None, action: str, reason: str | None = None
) -> None:
    session.add(ContentPackageVersion(content_package_id=package.id, version=package.review_version, status=package.status, actor_id=actor_id, action=action, reason=reason, snapshot_json=_snapshot(package)))


def request_content_package_generation(
    session: Session, actor_id: uuid.UUID, clip: ProductionClip, rerun: bool = False
) -> ContentPackage:
    settings = get_settings()
    if not settings.content_package_enabled:
        raise ProductionError("CONTENT_PACKAGES_DISABLED", "content package generation is disabled")
    if clip.render_status != "SUCCEEDED":
        raise ProductionError("CLIP_NOT_RENDERED", "a successfully rendered clip is required")
    latest = session.scalar(
        select(ContentPackage)
        .where(ContentPackage.clip_id == clip.id)
        .order_by(ContentPackage.generation_version.desc())
    )
    if latest is not None and not rerun:
        return latest
    if latest is not None and latest.status in {ContentPackageStatus.QUEUED, ContentPackageStatus.GENERATING}:
        raise ProductionError("CONTENT_PACKAGE_GENERATION_RUNNING", "content package generation is already running")
    if latest is not None:
        latest.status = ContentPackageStatus.STALE
        latest.review_version += 1
        _record_version(session, latest, actor_id, "content_package.superseded", "Explicit regeneration requested.")
    package = ContentPackage(
        clip_id=clip.id,
        project_id=clip.project_id,
        brand_id=clip.brand_id,
        generation_version=(latest.generation_version + 1) if latest is not None else 1,
        status=ContentPackageStatus.QUEUED,
        provider_name=settings.content_package_provider,
        model_name=None,
        provider_version=None,
        language="und",
        content_category="SOURCE_CLIP",
        confidence=0.0,
        explanation="Queued for evidence-bound content package generation.",
    )
    session.add(package)
    session.flush()
    _record_version(session, package, actor_id, "content_package.queued")
    session.add(AuditEvent(actor_id=actor_id, entity_type="content_package", entity_id=package.id, brand_id=package.brand_id, event_name="content_package.queued", payload={"clip_id": str(clip.id), "generation_version": package.generation_version}))
    session.commit()
    return package


def execute_content_package_generation(
    session: Session, actor_id: uuid.UUID | None, package: ContentPackage, provider: ContentPackageProvider | None = None, settings: Settings | None = None
) -> ContentPackage:
    settings = settings or get_settings()
    if package.status != ContentPackageStatus.QUEUED:
        return package
    package.status = ContentPackageStatus.GENERATING
    session.add(AuditEvent(actor_id=actor_id, entity_type="content_package", entity_id=package.id, brand_id=package.brand_id, event_name="content_package.generation.started"))
    session.commit()
    try:
        clip = session.get(ProductionClip, package.clip_id)
        if clip is None or clip.render_status != "SUCCEEDED":
            raise ProductionError("CLIP_NOT_RENDERED", "a successfully rendered clip is required")
        draft = (provider or content_package_provider_for(settings)).generate(_context_for(session, clip, settings))
        package.provider_name = draft.provider_name
        package.model_name = draft.model_name
        package.provider_version = draft.provider_version
        package.language = draft.language
        package.content_category = draft.content_category
        package.confidence = max(0.0, min(1.0, draft.confidence))
        package.explanation = draft.explanation
        package.fields_json = draft.fields
        package.verified_facts_json = draft.verified_facts
        package.transcript_statements_json = draft.transcript_statements
        package.uncertainty_json = draft.uncertainty
        package.warnings_json = draft.warnings
        package.status = ContentPackageStatus.PENDING
        package.review_version += 1
        _record_version(session, package, actor_id, "content_package.generated")
        session.add(AuditEvent(actor_id=actor_id, entity_type="content_package", entity_id=package.id, event_name="content_package.generated", payload={"provider": draft.provider_name, "generation_version": package.generation_version}))
        session.commit()
        return package
    except Exception as error:
        session.rollback()
        persisted = session.get(ContentPackage, package.id)
        if persisted is not None:
            persisted.status = ContentPackageStatus.REJECTED
            persisted.explanation = f"Generation failed: {type(error).__name__}."
            persisted.review_version += 1
            _record_version(session, persisted, actor_id, "content_package.generation.failed", type(error).__name__)
            session.add(AuditEvent(actor_id=actor_id, entity_type="content_package", entity_id=persisted.id, event_name="content_package.generation.failed", payload={"error": type(error).__name__}))
            session.commit()
        raise


def edit_content_package(
    session: Session, actor_id: uuid.UUID, package: ContentPackage, expected_version: int, fields: dict[str, object]
) -> ContentPackage:
    if package.review_version != expected_version:
        raise ProductionError("CONTENT_PACKAGE_VERSION_CONFLICT", "content package has changed; reload it before editing")
    if package.status not in {ContentPackageStatus.PENDING, ContentPackageStatus.APPROVED, ContentPackageStatus.REJECTED}:
        raise ProductionError("CONTENT_PACKAGE_NOT_EDITABLE", "content package is not ready for manual editing")
    package.fields_json = fields
    package.status = ContentPackageStatus.PENDING
    package.review_version += 1
    _record_version(session, package, actor_id, "content_package.edited")
    session.add(AuditEvent(actor_id=actor_id, entity_type="content_package", entity_id=package.id, event_name="content_package.edited"))
    session.commit()
    return package


def decide_content_package(
    session: Session, actor_id: uuid.UUID, package: ContentPackage, expected_version: int, approved: bool, reason: str | None = None
) -> ContentPackage:
    target = ContentPackageStatus.APPROVED if approved else ContentPackageStatus.REJECTED
    if package.status == target:
        return package
    if package.review_version != expected_version:
        raise ProductionError("CONTENT_PACKAGE_VERSION_CONFLICT", "content package has changed; reload it before reviewing")
    if package.status != ContentPackageStatus.PENDING:
        raise ProductionError("CONTENT_PACKAGE_NOT_REVIEWABLE", "content package is not pending review")
    package.status = target
    package.review_version += 1
    _record_version(session, package, actor_id, "content_package.approved" if approved else "content_package.rejected", reason)
    session.add(AuditEvent(actor_id=actor_id, entity_type="content_package", entity_id=package.id, event_name="content_package.approved" if approved else "content_package.rejected", payload={"reason": reason} if reason else {}))
    session.commit()
    return package
