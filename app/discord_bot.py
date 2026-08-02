"""Focused Discord control plane for the persisted clipping MVP.

The bot is deliberately optional: the API does not import or start it.
"""

import asyncio
import contextlib
import shutil
import uuid
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path

import discord
from discord import app_commands
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Executable

from app.accounts.models import Role, RoleName, User, UserRole
from app.analysis.models import AnalysisEvent, AnalysisSegment, TranscriptSegment, VideoAnalysis
from app.analysis.service import request_analysis
from app.analytics.service import dashboard as analytics_dashboard
from app.audit.models import AuditEvent
from app.brands.models import Brand, BrandMembership
from app.brands.service import ensure_legacy_brand, set_default_brand
from app.common.config import Settings, get_settings
from app.common.db import get_session
from app.content_packages.models import ContentPackage, ContentPackageStatus
from app.content_packages.service import (
    decide_content_package,
    edit_content_package,
    request_content_package_generation,
)
from app.discord_business.discord import (
    apply_business_presence,
    business_presence_interval,
    is_business_staff,
    register_business_commands,
)
from app.discord_business.operations import OperationsRepository, scan_message
from app.discord_business.service import BusinessRepository, load_config
from app.discovery.models import DiscoveredMedia, DiscoveryRun, DiscoverySource, DiscoveryStatus
from app.discovery.service import approve_media, reject_media, run_source
from app.ingestion.storage import LocalFilesystemStorage
from app.media_preview.service import IssuedPreview, PreviewError, issue_preview
from app.opportunities.models import (
    ClipOpportunity,
    OpportunityGenerationRun,
    OpportunityReason,
    OpportunityReviewStatus,
)
from app.opportunities.service import (
    decide_opportunity,
    request_opportunity_generation,
)
from app.production.models import (
    PostingQueueItem,
    ProductionClip,
    ProductionProject,
    ProductionSource,
)
from app.production.service import (
    ProductionError,
    YtDlpDownloadProvider,
    accept_source,
    approve_all,
    choose_source,
    create_project,
    decide_clip,
    download_project,
    generate_clips,
    reject_source,
    set_caption,
)
from app.production.youtube import YouTubeChannel, resolve_youtube_channel
from app.publishing.models import PublishRequest
from app.publishing.service import PublishingError, cancel_publish, confirm_publish


def configured_role_ids(settings: Settings) -> frozenset[int]:
    if not settings.discord_allowed_role_ids:
        return frozenset()
    try:
        return frozenset(
            int(value.strip())
            for value in settings.discord_allowed_role_ids.split(",")
            if value.strip()
        )
    except ValueError:
        return frozenset()


def is_authorized(member: discord.Member, settings: Settings) -> bool:
    if member.guild.owner_id == member.id:
        return True
    allowed = configured_role_ids(settings)
    return bool(allowed and allowed.intersection(role.id for role in member.roles))


@dataclass(frozen=True)
class DashboardState:
    project: ProductionProject
    source: ProductionSource | None
    total_clips: int
    approved: int
    rejected: int
    queued: int
    analysis: VideoAnalysis | None
    analysis_segment_count: int
    transcript_segment_count: int
    analysis_event_count: int
    scene_count: int
    speech_duration_seconds: float
    silence_duration_seconds: float
    motion_event_count: int
    loud_audio_event_count: int
    opportunity_count: int
    pending_opportunity_count: int
    approved_opportunity_count: int


@dataclass(frozen=True)
class ReviewState:
    clip: ProductionClip
    project_id: uuid.UUID
    position: int
    total: int


@dataclass(frozen=True)
class OpportunityReviewState:
    opportunity: ClipOpportunity
    reasons: list[OpportunityReason]
    transcript_preview: str
    position: int
    total: int


@dataclass(frozen=True)
class ControlCenterState:
    active_brand_name: str
    total_projects: int
    source_review_count: int
    source_ready_count: int
    discovery_pending_count: int
    discovery_source_count: int
    analysis_queued_count: int
    analysis_running_count: int
    analysis_completed_count: int
    analysis_failed_count: int
    opportunity_pending_count: int
    clip_pending_count: int
    queue_ready_count: int
    failure_count: int


class ProductionRepository:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def _session(self) -> Generator[Session, None, None]:
        return get_session()

    def _actor(self, session: Session) -> uuid.UUID:
        statement = (
            select(User.id)
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(User.is_active, Role.name.in_([RoleName.OWNER, RoleName.ADMIN]))
            .order_by(User.created_at)
        )
        actor = session.scalar(statement)
        if actor is None:
            raise ProductionError(
                "DISCORD_ACTOR_UNAVAILABLE", "Discord bot requires an active owner or admin user"
            )
        return actor

    def _storage(self) -> LocalFilesystemStorage:
        from pathlib import Path

        return LocalFilesystemStorage(Path(self.settings.local_storage_root))

    def default_brand(self) -> Brand:
        session = next(self._session())
        try:
            return self._default_brand_in_session(session)
        finally:
            session.close()

    def _default_brand_in_session(self, session: Session) -> Brand:
        actor = self._actor(session)
        membership = session.scalar(
            select(BrandMembership)
            .where(BrandMembership.user_id == actor, BrandMembership.is_default)
            .order_by(BrandMembership.created_at)
        )
        if membership is None:
            brand = ensure_legacy_brand(session)
            session.add(
                BrandMembership(brand_id=brand.id, user_id=actor, role="ADMIN", is_default=True)
            )
            session.commit()
            return brand
        selected_brand = session.get(Brand, membership.brand_id)
        assert selected_brand is not None
        return selected_brand

    def brands(self) -> list[Brand]:
        session = next(self._session())
        try:
            actor = self._actor(session)
            return list(
                session.scalars(
                    select(Brand)
                    .join(BrandMembership)
                    .where(BrandMembership.user_id == actor, Brand.is_active)
                    .order_by(Brand.name)
                )
            )
        finally:
            session.close()

    def select_brand(self, brand_id: uuid.UUID) -> Brand:
        session = next(self._session())
        try:
            actor = self._actor(session)
            set_default_brand(session, actor, brand_id)
            brand = session.get(Brand, brand_id)
            assert brand is not None
            return brand
        finally:
            session.close()

    def create_project(self, url: str) -> ProductionProject:
        session = next(self._session())
        try:
            return create_project(session, self._actor(session), url)
        finally:
            session.close()

    def dashboard(self, project_id: uuid.UUID) -> DashboardState:
        session = next(self._session())
        try:
            project = session.get(ProductionProject, project_id)
            if project is None:
                raise ProductionError("PROJECT_NOT_FOUND", "project no longer exists")
            clips = select(ProductionClip).where(ProductionClip.project_id == project_id).subquery()
            total = session.scalar(select(func.count()).select_from(clips)) or 0
            approved = (
                session.scalar(
                    select(func.count())
                    .select_from(clips)
                    .where(clips.c.approval_status == "APPROVED")
                )
                or 0
            )
            rejected = (
                session.scalar(
                    select(func.count())
                    .select_from(clips)
                    .where(clips.c.approval_status == "REJECTED")
                )
                or 0
            )
            queued = (
                session.scalar(
                    select(func.count())
                    .select_from(PostingQueueItem)
                    .join(ProductionClip, PostingQueueItem.clip_id == ProductionClip.id)
                    .where(
                        ProductionClip.project_id == project_id,
                        PostingQueueItem.status == "READY_TO_POST",
                    )
                )
                or 0
            )
            source = (
                session.get(ProductionSource, project.selected_source_id)
                if project.selected_source_id
                else None
            )
            analysis = session.scalar(
                select(VideoAnalysis)
                .where(VideoAnalysis.project_id == project_id)
                .order_by(VideoAnalysis.created_at.desc())
            )
            segment_count = 0
            transcript_count = 0
            event_count = 0
            scene_count = 0
            speech_duration = 0.0
            silence_duration = 0.0
            motion_event_count = 0
            loud_audio_event_count = 0
            opportunity_count = 0
            pending_opportunity_count = 0
            approved_opportunity_count = 0
            if analysis is not None:
                segment_count = (
                    session.scalar(
                        select(func.count()).where(AnalysisSegment.analysis_id == analysis.id)
                    )
                    or 0
                )
                transcript_count = (
                    session.scalar(
                        select(func.count()).where(TranscriptSegment.analysis_id == analysis.id)
                    )
                    or 0
                )
                event_count = (
                    session.scalar(
                        select(func.count()).where(AnalysisEvent.analysis_id == analysis.id)
                    )
                    or 0
                )
                scene_count = (
                    session.scalar(
                        select(func.count()).where(
                            AnalysisSegment.analysis_id == analysis.id,
                            AnalysisSegment.segment_type == "SCENE",
                        )
                    )
                    or 0
                )
                speech_duration = float(
                    session.scalar(
                        select(
                            func.coalesce(
                                func.sum(AnalysisSegment.end_time - AnalysisSegment.start_time), 0.0
                            )
                        ).where(
                            AnalysisSegment.analysis_id == analysis.id,
                            AnalysisSegment.segment_type == "SPEECH",
                        )
                    )
                    or 0.0
                )
                silence_duration = float(
                    session.scalar(
                        select(
                            func.coalesce(
                                func.sum(AnalysisSegment.end_time - AnalysisSegment.start_time), 0.0
                            )
                        ).where(
                            AnalysisSegment.analysis_id == analysis.id,
                            AnalysisSegment.segment_type == "SILENCE",
                        )
                    )
                    or 0.0
                )
                motion_event_count = (
                    session.scalar(
                        select(func.count()).where(
                            AnalysisEvent.analysis_id == analysis.id,
                            AnalysisEvent.event_type == "MOTION_SPIKE",
                        )
                    )
                    or 0
                )
                loud_audio_event_count = (
                    session.scalar(
                        select(func.count()).where(
                            AnalysisEvent.analysis_id == analysis.id,
                            AnalysisEvent.event_type == "AUDIO_PEAK",
                        )
                    )
                    or 0
                )
            latest_opportunity_run = session.scalar(
                select(OpportunityGenerationRun)
                .where(OpportunityGenerationRun.project_id == project_id)
                .order_by(OpportunityGenerationRun.generation_version.desc())
            )
            if latest_opportunity_run is not None:
                opportunity_count = (
                    session.scalar(
                        select(func.count()).where(
                            ClipOpportunity.project_id == project_id,
                            ClipOpportunity.generation_version
                            == latest_opportunity_run.generation_version,
                        )
                    )
                    or 0
                )
                approved_opportunity_count = (
                    session.scalar(
                        select(func.count()).where(
                            ClipOpportunity.project_id == project_id,
                            ClipOpportunity.generation_version
                            == latest_opportunity_run.generation_version,
                            ClipOpportunity.review_status == OpportunityReviewStatus.APPROVED,
                        )
                    )
                    or 0
                )
                pending_opportunity_count = (
                    session.scalar(
                        select(func.count()).where(
                            ClipOpportunity.project_id == project_id,
                            ClipOpportunity.generation_version
                            == latest_opportunity_run.generation_version,
                            ClipOpportunity.review_status == OpportunityReviewStatus.PENDING,
                        )
                    )
                    or 0
                )
            return DashboardState(
                project,
                source,
                total,
                approved,
                rejected,
                queued,
                analysis,
                segment_count,
                transcript_count,
                event_count,
                scene_count,
                speech_duration,
                silence_duration,
                motion_event_count,
                loud_audio_event_count,
                opportunity_count,
                pending_opportunity_count,
                approved_opportunity_count,
            )
        finally:
            session.close()

    def set_dashboard_message(
        self, project_id: uuid.UUID, guild_id: int, channel_id: int, message_id: int
    ) -> None:
        session = next(self._session())
        try:
            project = session.get(ProductionProject, project_id)
            if project is not None:
                project.discord_guild_id, project.discord_channel_id, project.discord_message_id = (
                    str(guild_id),
                    str(channel_id),
                    str(message_id),
                )
                session.commit()
        finally:
            session.close()

    def download(self, project_id: uuid.UUID) -> ProductionProject:
        session = next(self._session())
        try:
            project = session.get(ProductionProject, project_id)
            if project is None:
                raise ProductionError("PROJECT_NOT_FOUND", "project no longer exists")
            return download_project(session, self._actor(session), project, self._storage())
        finally:
            session.close()

    def sources(self, project_id: uuid.UUID) -> list[ProductionSource]:
        session = next(self._session())
        try:
            return list(
                session.scalars(
                    select(ProductionSource)
                    .where(ProductionSource.project_id == project_id)
                    .order_by(ProductionSource.quality_score.desc(), ProductionSource.created_at)
                )
            )
        finally:
            session.close()

    def accept_source(self, project_id: uuid.UUID) -> ProductionProject:
        session = next(self._session())
        try:
            project = session.get(ProductionProject, project_id)
            if project is None:
                raise ProductionError("PROJECT_NOT_FOUND", "project no longer exists")
            accepted = accept_source(session, self._actor(session), project)
            # A source decision is a human gate. Once it has been made, the
            # persisted, idempotent worker pipeline owns the safe mechanical work.
            from app.worker import process_accepted_source

            process_accepted_source.delay(str(accepted.id))
            return accepted
        finally:
            session.close()

    def reject_source(self, project_id: uuid.UUID) -> ProductionProject:
        session = next(self._session())
        try:
            project = session.get(ProductionProject, project_id)
            if project is None:
                raise ProductionError("PROJECT_NOT_FOUND", "project no longer exists")
            return reject_source(session, self._actor(session), project)
        finally:
            session.close()

    def choose_source(
        self, project_id: uuid.UUID, source_id: uuid.UUID, expected_version: int
    ) -> ProductionProject:
        session = next(self._session())
        try:
            project = session.get(ProductionProject, project_id)
            if project is None:
                raise ProductionError("PROJECT_NOT_FOUND", "project no longer exists")
            return choose_source(
                session, self._actor(session), project, source_id, expected_version
            )
        finally:
            session.close()

    def generate(self, project_id: uuid.UUID) -> list[ProductionClip]:
        session = next(self._session())
        try:
            project = session.get(ProductionProject, project_id)
            if project is None:
                raise ProductionError("PROJECT_NOT_FOUND", "project no longer exists")
            return generate_clips(session, self._actor(session), project, self._storage())
        finally:
            session.close()

    def clips(self, project_id: uuid.UUID) -> list[ProductionClip]:
        session = next(self._session())
        try:
            return list(
                session.scalars(
                    select(ProductionClip)
                    .where(ProductionClip.project_id == project_id)
                    .order_by(ProductionClip.clip_number)
                )
            )
        finally:
            session.close()

    def request_opportunities(self, project_id: uuid.UUID, rerun: bool = False) -> None:
        session = next(self._session())
        try:
            analysis = session.scalar(
                select(VideoAnalysis)
                .where(VideoAnalysis.project_id == project_id, VideoAnalysis.status == "COMPLETED")
                .order_by(VideoAnalysis.completed_at.desc())
            )
            if analysis is None:
                raise ProductionError("ANALYSIS_NOT_READY", "complete analysis is required")
            request_opportunity_generation(session, self._actor(session), analysis, rerun=rerun)
            from app.worker import generate_clip_opportunities

            generate_clip_opportunities.delay(str(analysis.id), rerun=rerun)
        finally:
            session.close()

    def request_analysis(self, project_id: uuid.UUID, rerun: bool = False) -> VideoAnalysis:
        session = next(self._session())
        try:
            project = session.get(ProductionProject, project_id)
            if project is None:
                raise ProductionError("PROJECT_NOT_FOUND", "project no longer exists")
            analysis = request_analysis(session, self._actor(session), project, rerun=rerun)
            from app.worker import run_video_analysis

            run_video_analysis.delay(str(project.id), rerun=rerun)
            return analysis
        finally:
            session.close()

    def analysis_text_page(
        self, project_id: uuid.UUID, kind: str, page: int, page_size: int = 8
    ) -> tuple[list[str], int]:
        session = next(self._session())
        try:
            analysis = session.scalar(
                select(VideoAnalysis)
                .where(VideoAnalysis.project_id == project_id)
                .order_by(VideoAnalysis.created_at.desc())
            )
            if analysis is None:
                raise ProductionError("ANALYSIS_NOT_READY", "analysis is not available")
            if kind == "transcript":
                items = list(
                    session.scalars(
                        select(TranscriptSegment)
                        .where(TranscriptSegment.analysis_id == analysis.id)
                        .order_by(TranscriptSegment.start_time)
                    )
                )
                lines = [
                    f"{item.start_time:.1f}s–{item.end_time:.1f}s: {item.text[:180]}"
                    for item in items
                ]
            else:
                segments = list(
                    session.scalars(
                        select(AnalysisSegment)
                        .where(AnalysisSegment.analysis_id == analysis.id)
                        .order_by(AnalysisSegment.start_time)
                    )
                )
                events = list(
                    session.scalars(
                        select(AnalysisEvent)
                        .where(AnalysisEvent.analysis_id == analysis.id)
                        .order_by(AnalysisEvent.timestamp)
                    )
                )
                lines = sorted(
                    [
                        f"{item.start_time:.1f}s–{item.end_time:.1f}s: {item.segment_type}"
                        for item in segments
                    ]
                    + [f"{item.timestamp:.1f}s: {item.event_type}" for item in events]
                )
            start = max(0, page) * page_size
            return lines[start : start + page_size], len(lines)
        finally:
            session.close()

    def opportunity_state(
        self, opportunity_id: uuid.UUID, direction: int = 0
    ) -> OpportunityReviewState:
        session = next(self._session())
        try:
            current = session.get(ClipOpportunity, opportunity_id)
            if current is None:
                raise ProductionError("OPPORTUNITY_NOT_FOUND", "opportunity no longer exists")
            opportunities = list(
                session.scalars(
                    select(ClipOpportunity)
                    .where(
                        ClipOpportunity.project_id == current.project_id,
                        ClipOpportunity.generation_version == current.generation_version,
                        ClipOpportunity.review_status != OpportunityReviewStatus.STALE,
                    )
                    .order_by(ClipOpportunity.overall_score.desc(), ClipOpportunity.start_time)
                )
            )
            position = next(
                (index for index, item in enumerate(opportunities) if item.id == current.id), None
            )
            if position is None or position + direction not in range(len(opportunities)):
                raise ProductionError(
                    "OPPORTUNITY_NAVIGATION_BOUNDARY", "no opportunity exists in that direction"
                )
            target = opportunities[position + direction]
            reasons = list(
                session.scalars(
                    select(OpportunityReason)
                    .where(OpportunityReason.opportunity_id == target.id)
                    .order_by(OpportunityReason.weight.desc(), OpportunityReason.reason_type)
                )
            )
            transcript = list(
                session.scalars(
                    select(TranscriptSegment)
                    .where(
                        TranscriptSegment.analysis_id == target.analysis_id,
                        TranscriptSegment.end_time >= target.start_time,
                        TranscriptSegment.start_time <= target.end_time,
                    )
                    .order_by(TranscriptSegment.start_time)
                    .limit(3)
                )
            )
            return OpportunityReviewState(
                target,
                reasons,
                " ".join(item.text for item in transcript)[:500] or "No transcript available.",
                position + direction,
                len(opportunities),
            )
        finally:
            session.close()

    def decide_opportunity(self, opportunity_id: uuid.UUID, approved: bool) -> ClipOpportunity:
        session = next(self._session())
        try:
            opportunity = session.get(ClipOpportunity, opportunity_id)
            if opportunity is None:
                raise ProductionError("OPPORTUNITY_NOT_FOUND", "opportunity no longer exists")
            actor = self._actor(session)
            result = decide_opportunity(
                session, actor, opportunity, approved, opportunity.review_version
            )
            if approved:
                # Rendering and previews are mechanical, potentially long-running work.
                # Keep the Discord interaction responsive and let the idempotent worker
                # resume it safely after a restart.
                from app.worker import render_approved_opportunity

                render_approved_opportunity.delay(str(result.id))
            return result
        finally:
            session.close()

    def opportunities(self, project_id: uuid.UUID) -> list[ClipOpportunity]:
        session = next(self._session())
        try:
            run = session.scalar(
                select(OpportunityGenerationRun)
                .where(OpportunityGenerationRun.project_id == project_id)
                .order_by(OpportunityGenerationRun.generation_version.desc())
            )
            if run is None:
                return []
            return list(
                session.scalars(
                    select(ClipOpportunity)
                    .where(
                        ClipOpportunity.project_id == project_id,
                        ClipOpportunity.generation_version == run.generation_version,
                        ClipOpportunity.review_status != OpportunityReviewStatus.STALE,
                    )
                    .order_by(ClipOpportunity.overall_score.desc(), ClipOpportunity.start_time)
                )
            )
        finally:
            session.close()

    def active_opportunity_ids(self) -> list[uuid.UUID]:
        session = next(self._session())
        try:
            return list(
                session.scalars(
                    select(ClipOpportunity.id)
                    .where(ClipOpportunity.review_status == OpportunityReviewStatus.PENDING)
                    .order_by(ClipOpportunity.created_at.desc())
                    .limit(100)
                )
            )
        finally:
            session.close()

    def review_state(self, clip_id: uuid.UUID, direction: int = 0) -> ReviewState:
        session = next(self._session())
        try:
            current = session.get(ProductionClip, clip_id)
            if current is None:
                raise ProductionError("CLIP_NOT_FOUND", "clip no longer exists")
            clips = list(
                session.scalars(
                    select(ProductionClip)
                    .where(
                        ProductionClip.project_id == current.project_id,
                        ProductionClip.render_status == "SUCCEEDED",
                    )
                    .order_by(ProductionClip.clip_number, ProductionClip.id)
                )
            )
            index = next(
                (number for number, clip in enumerate(clips) if clip.id == current.id), None
            )
            if index is None:
                raise ProductionError("CLIP_STALE", "clip is no longer reviewable")
            target = index + direction
            if target < 0 or target >= len(clips):
                raise ProductionError(
                    "CLIP_NAVIGATION_BOUNDARY", "no clip exists in that direction"
                )
            return ReviewState(clips[target], current.project_id, target, len(clips))
        finally:
            session.close()

    def decide(self, clip_id: uuid.UUID, approved: bool) -> ProductionClip:
        session = next(self._session())
        try:
            clip = session.get(ProductionClip, clip_id)
            if clip is None:
                raise ProductionError("CLIP_NOT_FOUND", "clip no longer exists")
            decided = decide_clip(session, self._actor(session), clip, approved)
            if approved:
                package = request_content_package_generation(
                    session, self._actor(session), decided
                )
                if package.status == ContentPackageStatus.QUEUED:
                    from app.worker import generate_content_package

                    generate_content_package.delay(str(decided.id))
            return decided
        finally:
            session.close()

    def approve_all(self, project_id: uuid.UUID) -> int:
        session = next(self._session())
        try:
            return approve_all(session, self._actor(session), project_id)
        finally:
            session.close()

    def caption(self, clip_id: uuid.UUID, caption: str) -> ProductionClip:
        session = next(self._session())
        try:
            clip = session.get(ProductionClip, clip_id)
            if clip is None:
                raise ProductionError("CLIP_NOT_FOUND", "clip no longer exists")
            return set_caption(session, self._actor(session), clip, caption)
        finally:
            session.close()

    def content_package(self, clip_id: uuid.UUID) -> ContentPackage | None:
        session = next(self._session())
        try:
            return session.scalar(
                select(ContentPackage)
                .where(ContentPackage.clip_id == clip_id)
                .order_by(ContentPackage.generation_version.desc())
            )
        finally:
            session.close()

    def request_content_package(self, clip_id: uuid.UUID, rerun: bool = False) -> ContentPackage:
        session = next(self._session())
        try:
            clip = session.get(ProductionClip, clip_id)
            if clip is None:
                raise ProductionError("CLIP_NOT_FOUND", "clip no longer exists")
            return request_content_package_generation(session, self._actor(session), clip, rerun)
        finally:
            session.close()

    def edit_content_package(
        self, package_id: uuid.UUID, expected_version: int, fields: dict[str, object]
    ) -> ContentPackage:
        session = next(self._session())
        try:
            package = session.get(ContentPackage, package_id)
            if package is None:
                raise ProductionError(
                    "CONTENT_PACKAGE_NOT_FOUND", "content package no longer exists"
                )
            return edit_content_package(
                session, self._actor(session), package, expected_version, fields
            )
        finally:
            session.close()

    def decide_content_package(
        self, package_id: uuid.UUID, expected_version: int, approved: bool
    ) -> ContentPackage:
        session = next(self._session())
        try:
            package = session.get(ContentPackage, package_id)
            if package is None:
                raise ProductionError(
                    "CONTENT_PACKAGE_NOT_FOUND", "content package no longer exists"
                )
            return decide_content_package(
                session, self._actor(session), package, expected_version, approved
            )
        finally:
            session.close()

    def queue(self) -> list[tuple[PostingQueueItem, ProductionClip, ProductionProject]]:
        session = next(self._session())
        try:
            brand = self._default_brand_in_session(session)
            return list(
                session.execute(
                    select(PostingQueueItem, ProductionClip, ProductionProject)
                    .join(ProductionClip, PostingQueueItem.clip_id == ProductionClip.id)
                    .join(ProductionProject, ProductionClip.project_id == ProductionProject.id)
                    .where(
                        PostingQueueItem.status == "READY_TO_POST",
                        PostingQueueItem.brand_id == brand.id,
                    )
                    .order_by(PostingQueueItem.created_at)
                ).tuples()
            )
        finally:
            session.close()

    def publish_request(self, request_id: uuid.UUID) -> PublishRequest | None:
        session = next(self._session())
        try:
            return session.get(PublishRequest, request_id)
        finally:
            session.close()

    def confirm_publish_request(self, request_id: uuid.UUID) -> PublishRequest:
        session = next(self._session())
        try:
            request = session.get(PublishRequest, request_id)
            if request is None:
                raise ProductionError(
                    "PUBLISH_REQUEST_NOT_FOUND", "publishing request no longer exists"
                )
            confirmed = confirm_publish(session, self._actor(session), request)
            if confirmed.status == "QUEUED" and self.settings.publishing_enabled:
                from app.worker import execute_publish_request

                execute_publish_request.delay(str(confirmed.id))
            return confirmed
        finally:
            session.close()

    def cancel_publish_request(self, request_id: uuid.UUID) -> PublishRequest:
        session = next(self._session())
        try:
            request = session.get(PublishRequest, request_id)
            if request is None:
                raise ProductionError(
                    "PUBLISH_REQUEST_NOT_FOUND", "publishing request no longer exists"
                )
            return cancel_publish(session, self._actor(session), request)
        finally:
            session.close()

    def control_center(self) -> ControlCenterState:
        """Return compact, read-only operational counts for the Discord home view."""
        session = next(self._session())
        try:
            brand = self._default_brand_in_session(session)
            brand_id = brand.id

            def count(statement: Executable) -> int:
                return int(session.scalar(statement) or 0)

            return ControlCenterState(
                active_brand_name=brand.name,
                total_projects=count(
                    select(func.count())
                    .select_from(ProductionProject)
                    .where(ProductionProject.brand_id == brand_id)
                ),
                source_review_count=count(
                    select(func.count())
                    .select_from(ProductionProject)
                    .where(
                        ProductionProject.status == "SOURCE_REVIEW_REQUIRED",
                        ProductionProject.brand_id == brand_id,
                    )
                ),
                source_ready_count=count(
                    select(func.count())
                    .select_from(ProductionProject)
                    .where(
                        ProductionProject.status == "SOURCE_READY",
                        ProductionProject.brand_id == brand_id,
                    )
                ),
                discovery_pending_count=count(
                    select(func.count())
                    .select_from(DiscoveredMedia)
                    .where(
                        DiscoveredMedia.lifecycle_status == DiscoveryStatus.NEEDS_REVIEW,
                        DiscoveredMedia.brand_id == brand_id,
                    )
                ),
                discovery_source_count=count(
                    select(func.count())
                    .select_from(DiscoverySource)
                    .where(DiscoverySource.enabled, DiscoverySource.brand_id == brand_id)
                ),
                analysis_queued_count=count(
                    select(func.count())
                    .select_from(VideoAnalysis)
                    .where(VideoAnalysis.status == "QUEUED", VideoAnalysis.brand_id == brand_id)
                ),
                analysis_running_count=count(
                    select(func.count())
                    .select_from(VideoAnalysis)
                    .where(VideoAnalysis.status == "RUNNING", VideoAnalysis.brand_id == brand_id)
                ),
                analysis_completed_count=count(
                    select(func.count())
                    .select_from(VideoAnalysis)
                    .where(VideoAnalysis.status == "COMPLETED", VideoAnalysis.brand_id == brand_id)
                ),
                analysis_failed_count=count(
                    select(func.count())
                    .select_from(VideoAnalysis)
                    .where(VideoAnalysis.status == "FAILED", VideoAnalysis.brand_id == brand_id)
                ),
                opportunity_pending_count=count(
                    select(func.count())
                    .select_from(ClipOpportunity)
                    .where(
                        ClipOpportunity.review_status == OpportunityReviewStatus.PENDING,
                        ClipOpportunity.brand_id == brand_id,
                    )
                ),
                clip_pending_count=count(
                    select(func.count())
                    .select_from(ProductionClip)
                    .where(
                        ProductionClip.render_status == "SUCCEEDED",
                        ProductionClip.approval_status == "PENDING",
                        ProductionClip.brand_id == brand_id,
                    )
                ),
                queue_ready_count=count(
                    select(func.count())
                    .select_from(PostingQueueItem)
                    .where(
                        PostingQueueItem.status == "READY_TO_POST",
                        PostingQueueItem.brand_id == brand_id,
                    )
                ),
                failure_count=count(
                    select(func.count())
                    .select_from(ProductionProject)
                    .where(
                        ProductionProject.status.like("%FAILED%"),
                        ProductionProject.brand_id == brand_id,
                    )
                ),
            )
        finally:
            session.close()

    def projects(self, status: str | None = None, limit: int = 25) -> list[ProductionProject]:
        session = next(self._session())
        try:
            brand = self._default_brand_in_session(session)
            statement = (
                select(ProductionProject)
                .where(ProductionProject.brand_id == brand.id)
                .order_by(ProductionProject.created_at.desc())
                .limit(limit)
            )
            if status:
                statement = statement.where(ProductionProject.status == status)
            return list(session.scalars(statement))
        finally:
            session.close()

    def first_pending_clip(self) -> ReviewState | None:
        session = next(self._session())
        try:
            brand = self._default_brand_in_session(session)
            clip_id = session.scalar(
                select(ProductionClip.id)
                .where(
                    ProductionClip.render_status == "SUCCEEDED",
                    ProductionClip.approval_status == "PENDING",
                    ProductionClip.brand_id == brand.id,
                )
                .order_by(ProductionClip.created_at)
            )
        finally:
            session.close()
        return self.review_state(clip_id) if clip_id else None

    def first_pending_opportunity(self) -> OpportunityReviewState | None:
        session = next(self._session())
        try:
            brand = self._default_brand_in_session(session)
            opportunity_id = session.scalar(
                select(ClipOpportunity.id)
                .where(
                    ClipOpportunity.review_status == OpportunityReviewStatus.PENDING,
                    ClipOpportunity.brand_id == brand.id,
                )
                .order_by(ClipOpportunity.created_at)
            )
        finally:
            session.close()
        return self.opportunity_state(opportunity_id) if opportunity_id else None

    def first_pending_opportunity_for_project(
        self, project_id: uuid.UUID
    ) -> OpportunityReviewState | None:
        """Return the next pending suggestion for one project.

        A guided project card is already scoped to a project.  It must not
        depend on the operator's currently selected brand, since a legacy or
        previously selected project can still legitimately need review.
        """
        session = next(self._session())
        try:
            run = session.scalar(
                select(OpportunityGenerationRun)
                .where(OpportunityGenerationRun.project_id == project_id)
                .order_by(OpportunityGenerationRun.generation_version.desc())
            )
            if run is None:
                return None
            opportunity_id = session.scalar(
                select(ClipOpportunity.id)
                .where(
                    ClipOpportunity.project_id == project_id,
                    ClipOpportunity.generation_version == run.generation_version,
                    ClipOpportunity.review_status == OpportunityReviewStatus.PENDING,
                )
                .order_by(ClipOpportunity.overall_score.desc(), ClipOpportunity.start_time)
            )
        finally:
            session.close()
        return self.opportunity_state(opportunity_id) if opportunity_id else None

    def active_dashboard_projects(self) -> list[uuid.UUID]:
        session = next(self._session())
        try:
            return list(
                session.scalars(
                    select(ProductionProject.id).where(
                        ProductionProject.discord_message_id.is_not(None),
                        ProductionProject.status.not_in(["FAILED", "ARCHIVED"]),
                    )
                )
            )
        finally:
            session.close()

    def active_review_clips(self) -> list[uuid.UUID]:
        session = next(self._session())
        try:
            return list(
                session.scalars(
                    select(ProductionClip.id)
                    .where(
                        ProductionClip.discord_message_id.is_not(None),
                        ProductionClip.render_status == "SUCCEEDED",
                    )
                    .order_by(
                        ProductionClip.project_id, ProductionClip.clip_number, ProductionClip.id
                    )
                )
            )
        finally:
            session.close()

    def set_clip_message(self, clip_id: uuid.UUID, message_id: int) -> None:
        session = next(self._session())
        try:
            clip = session.get(ProductionClip, clip_id)
            if clip is not None:
                clip.discord_message_id = str(message_id)
                session.commit()
        finally:
            session.close()

    def preview(self, clip_id: uuid.UUID) -> bytes | None:
        session = next(self._session())
        try:
            clip = session.get(ProductionClip, clip_id)
            if clip is None or not clip.storage_key:
                return None
            storage = self._storage()
            if (
                storage.metadata(clip.storage_key).size_bytes
                > self.settings.discord_max_preview_upload_bytes
            ):
                return None
            with storage.open(clip.storage_key) as handle:
                return handle.read()
        finally:
            session.close()

    def create_preview(self, clip_id: uuid.UUID, refresh: bool = False) -> IssuedPreview:
        session = next(self._session())
        try:
            clip = session.get(ProductionClip, clip_id)
            if clip is None:
                raise ProductionError("CLIP_NOT_FOUND", "clip no longer exists")
            try:
                return issue_preview(
                    session,
                    self._actor(session),
                    clip,
                    self._storage(),
                    self.settings,
                    refresh=refresh,
                )
            except PreviewError as error:
                raise ProductionError(error.code, str(error)) from error
        finally:
            session.close()


class DiscoveryRepository(ProductionRepository):
    def preview_youtube_channel(self, reference: str) -> YouTubeChannel:
        return asyncio.run(resolve_youtube_channel(reference, self.settings))

    def enable_youtube_channel(self, channel: YouTubeChannel) -> DiscoverySource:
        """Persist one explicitly validated public YouTube channel for the active brand."""
        session = next(self._session())
        try:
            actor = self._actor(session)
            brand = self._default_brand_in_session(session)
            existing = session.scalar(
                select(DiscoverySource).where(
                    DiscoverySource.brand_id == brand.id,
                    DiscoverySource.provider == "YOUTUBE",
                    DiscoverySource.account_identifier == channel.channel_id,
                )
            )
            if existing is not None:
                return existing
            source = DiscoverySource(
                brand_id=brand.id,
                name=channel.title,
                provider="YOUTUBE",
                source_type="CHANNEL",
                platform="YOUTUBE",
                account_identifier=channel.channel_id,
                public_url=channel.url,
                enabled=True,
                trusted=False,
                polling_interval_seconds=self.settings.discovery_default_polling_interval_seconds,
                configuration_json={"channel_id": channel.channel_id, "result_limit": 20},
            )
            session.add(source)
            session.flush()
            session.add(
                AuditEvent(
                    actor_id=actor,
                    entity_type="discovery_source",
                    entity_id=source.id,
                    brand_id=source.brand_id,
                    event_name="discovery.source.created_from_discord",
                    payload={"provider": "YOUTUBE", "channel_id": channel.channel_id},
                )
            )
            session.commit()
            return source
        finally:
            session.close()

    def media(self, media_id: uuid.UUID) -> DiscoveredMedia:
        session = next(self._session())
        try:
            media = session.get(DiscoveredMedia, media_id)
            if media is None:
                raise ProductionError("DISCOVERY_NOT_FOUND", "discovered item no longer exists")
            return media
        finally:
            session.close()

    def discovery_queue(self) -> list[DiscoveredMedia]:
        session = next(self._session())
        try:
            brand = self._default_brand_in_session(session)
            return list(
                session.scalars(
                    select(DiscoveredMedia)
                    .where(
                        DiscoveredMedia.lifecycle_status == DiscoveryStatus.NEEDS_REVIEW,
                        DiscoveredMedia.brand_id == brand.id,
                    )
                    .order_by(DiscoveredMedia.discovery_score.desc(), DiscoveredMedia.discovered_at)
                )
            )
        finally:
            session.close()

    def approve(self, media_id: uuid.UUID, expected_version: int) -> DiscoveredMedia:
        session = next(self._session())
        try:
            media = session.get(DiscoveredMedia, media_id)
            if media is None:
                raise ProductionError("DISCOVERY_NOT_FOUND", "discovered item no longer exists")
            return approve_media(session, self._actor(session), media, expected_version)
        finally:
            session.close()

    def reject(self, media_id: uuid.UUID, expected_version: int) -> DiscoveredMedia:
        session = next(self._session())
        try:
            media = session.get(DiscoveredMedia, media_id)
            if media is None:
                raise ProductionError("DISCOVERY_NOT_FOUND", "discovered item no longer exists")
            return reject_media(session, self._actor(session), media, expected_version)
        finally:
            session.close()

    def run(self, source_id: uuid.UUID) -> DiscoveryRun:
        session = next(self._session())
        try:
            source = session.get(DiscoverySource, source_id)
            if source is None:
                raise ProductionError(
                    "DISCOVERY_SOURCE_NOT_FOUND", "discovery source no longer exists"
                )
            return run_source(session, self._actor(session), source)
        finally:
            session.close()


def operational_status(settings: Settings | None = None) -> dict[str, bool]:
    settings = settings or get_settings()
    try:
        YtDlpDownloadProvider(settings).command_prefix()
        ytdlp = True
    except ProductionError:
        ytdlp = False
    return {
        "yt_dlp": ytdlp,
        "ffmpeg": bool(shutil.which(settings.ffmpeg_path) or Path(settings.ffmpeg_path).is_file()),
        "ffprobe": bool(
            shutil.which(settings.ffprobe_path) or Path(settings.ffprobe_path).is_file()
        ),
        "discord_configured": bool(settings.discord_bot_token),
        "youtube_search_configured": bool(settings.youtube_api_key),
    }


def user_error(error: ProductionError | PublishingError | str) -> str:
    """Return a safe operator-facing error with an actionable next step."""
    code = error.code if isinstance(error, (ProductionError, PublishingError)) else error
    guidance = {
        "PROJECT_NOT_FOUND": "The project was removed or this control is stale. Open Home and select it again.",
        "CLIP_NOT_FOUND": "This clip is no longer available. Refresh the review inbox.",
        "OPPORTUNITY_NOT_FOUND": "This opportunity is no longer available. Refresh the review inbox.",
        "DISCOVERY_NOT_FOUND": "This discovery item is no longer available. Refresh Discovery.",
        "ANALYSIS_NOT_READY": "Analysis must finish first. Use Refresh and try again once its status is COMPLETED.",
        "ANALYSIS_SOURCE_NOT_READY": "Download the accepted source before starting analysis.",
        "ANALYSIS_ALREADY_RUNNING": "Analysis is already in progress. Refresh shortly; no duplicate job was started.",
        "OPPORTUNITY_NAVIGATION_BOUNDARY": "There is no item in that direction. Use the enabled navigation controls.",
        "CLIP_NAVIGATION_BOUNDARY": "There is no item in that direction. Use the enabled navigation controls.",
        "STALE_SOURCE_ACTION": "Another operator changed the selected source. Refresh the project before deciding.",
        "STALE_OPPORTUNITY_ACTION": "Another operator reviewed this opportunity. Reopen the inbox before deciding.",
        "STALE_OPPORTUNITY": "This opportunity was superseded by a refreshed ranking. Reopen the inbox.",
        "YOUTUBE_NOT_CONFIGURED": "YouTube discovery is not configured on this server. Add VIRALFORGE_YOUTUBE_API_KEY to the protected VPS environment file, then restart Discord.",
        "YOUTUBE_CHANNEL_REFERENCE_INVALID": "Use a public YouTube channel URL, @handle, or channel ID.",
        "YOUTUBE_CHANNEL_NOT_FOUND": "YouTube could not find that public channel. Confirm its URL or @handle and try again.",
        "YOUTUBE_DISCOVERY_FAILED": "YouTube could not complete the official API request. Check that the API key is valid, YouTube Data API v3 is enabled, and project quota is available.",
    }
    detail = guidance.get(
        code,
        "The action could not be completed safely. Refresh and retry; no duplicate action was created.",
    )
    return f"**Action not completed**\n{detail}\nReference: `{code}`"


def unauthorized_message() -> str:
    return (
        "**You don't currently have the Operator role.**\n"
        "Choose an option below to see what is required or contact a server administrator."
    )


class OperatorAccessHelpView(discord.ui.View):
    """A safe, actionable permission response; this never grants a role itself."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(timeout=300)
        self.settings = settings

    @discord.ui.button(label="View Required Roles", style=discord.ButtonStyle.primary)
    async def required_roles(
        self, interaction: discord.Interaction, _: discord.ui.Button["OperatorAccessHelpView"]
    ) -> None:
        configured_roles = configured_role_ids(self.settings)
        role_summary = (
            ", ".join(f"<@&{role_id}>" for role_id in configured_roles)
            if configured_roles
            else "No operator roles have been configured yet."
        )
        await interaction.response.send_message(
            "**Required access**\n"
            f"A server owner must assign one of these configured roles: {role_summary}\n"
            "Ask an administrator to assign it, then use `/home` again.",
            ephemeral=True,
        )

    @discord.ui.button(label="Contact Administrator", style=discord.ButtonStyle.secondary)
    async def contact_administrator(
        self, interaction: discord.Interaction, _: discord.ui.Button["OperatorAccessHelpView"]
    ) -> None:
        await interaction.response.send_message(
            "Ask a server owner to assign the required ViralForge Operator role. "
            "ViralForge cannot grant roles automatically, which keeps workspace access protected.",
            ephemeral=True,
        )

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(
        self, interaction: discord.Interaction, _: discord.ui.Button["OperatorAccessHelpView"]
    ) -> None:
        await interaction.response.edit_message(
            content="Return to `/home` after an administrator has updated your role.", view=None
        )


def lifecycle_next_action(state: DashboardState) -> str:
    if state.project.status == "SOURCE_REVIEW_REQUIRED":
        return "Review and accept a source candidate."
    if (
        state.project.status in {"SOURCE_RESOLVED", "SOURCE_ACCEPTED", "SOURCE_READY"}
        and not state.project.source_storage_key
    ):
        return "Download the accepted source."
    if state.project.source_storage_key and state.analysis is None:
        return "Start analysis of the downloaded source."
    if state.analysis is not None and state.analysis.status in {"QUEUED", "RUNNING"}:
        return "Wait for the existing analysis job; Refresh for progress."
    if (
        state.analysis is not None
        and state.analysis.status == "COMPLETED"
        and not state.opportunity_count
    ):
        return "Detect ranked clip opportunities from the stored analysis."
    if state.pending_opportunity_count:
        return "Review the ranked opportunities."
    if state.opportunity_count and not state.total_clips:
        return "All ranked opportunities were reviewed; choose another source if none were selected."
    if state.total_clips and state.approved < state.total_clips:
        return "Review the rendered clips."
    return "Review the posting queue or open another project."


def dashboard_embed(state: DashboardState) -> discord.Embed:
    project = state.project
    embed = discord.Embed(
        title=project.source_title or "Production project",
        description=f"Status: **{project.status}**",
    )
    embed.add_field(name="Source", value=project.source_url, inline=False)
    embed.add_field(
        name="Duration",
        value=f"{project.source_duration_seconds:.1f}s"
        if project.source_duration_seconds
        else "Not downloaded",
        inline=True,
    )
    embed.add_field(name="Clips", value=str(state.total_clips), inline=True)
    embed.add_field(
        name="Approved / Rejected / Queued",
        value=f"{state.approved} / {state.rejected} / {state.queued}",
        inline=False,
    )
    embed.add_field(name="Next action", value=lifecycle_next_action(state), inline=False)
    if state.analysis is not None:
        analysis = state.analysis
        technical = (
            f"{analysis.width}x{analysis.height} @ {analysis.fps:.2f} fps"
            if analysis.width and analysis.height and analysis.fps is not None
            else "Technical metadata pending"
        )
        transcript = (
            f"{state.transcript_segment_count} segments ({analysis.transcript_language or 'language unavailable'})"
            if state.transcript_segment_count
            else "No transcript segments"
        )
        embed.add_field(
            name="Analysis",
            value=f"{analysis.status} · {technical}",
            inline=False,
        )
        embed.add_field(
            name="Analysis timeline",
            value=(
                f"{state.analysis_segment_count} segments · {state.analysis_event_count} events · "
                f"{state.scene_count} scenes · {state.speech_duration_seconds:.1f}s speech · {transcript}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Real analysis progress",
            value=(
                f"{analysis.analysis_version} · {analysis.current_stage or 'stage unavailable'} "
                f"{analysis.progress_percent:.0f}% · provider {analysis.metadata_json.get('provider', 'unknown')}"
            ),
            inline=False,
        )
        warnings = analysis.metadata_json.get("warnings", [])
        if isinstance(warnings, list) and warnings:
            embed.add_field(
                name="Analysis warnings",
                value="\n".join(str(item) for item in warnings)[:1024],
                inline=False,
            )
    if state.opportunity_count:
        embed.add_field(
            name="Clip opportunities",
            value=f"{state.opportunity_count} ranked · {state.approved_opportunity_count} approved",
            inline=False,
        )
    if state.source:
        source = state.source
        embed.add_field(
            name="Selected source",
            value=f"{source.platform} · {source.uploader_name or 'Unknown uploader'}",
            inline=False,
        )
        embed.add_field(
            name="Source quality",
            value=f"{source.quality_score:.0f}/100 · {source.quality_status}",
            inline=True,
        )
        embed.add_field(
            name="Original confidence",
            value=f"{source.original_source_confidence:.0%} · repost risk {source.repost_likelihood:.0%}",
            inline=True,
        )
        embed.add_field(
            name="Watermark",
            value=f"{source.watermark_status} ({source.watermark_confidence:.0%})",
            inline=False,
        )
        embed.add_field(
            name="Selected-source reason",
            value=(source.selected_source_reason or "No decision explanation available.")[:1024],
            inline=False,
        )
        if source.warnings:
            embed.add_field(
                name="Warnings",
                value="\n".join(f"• {warning}" for warning in source.warnings)[:1024],
                inline=False,
            )
    return embed


class ProjectDashboardView(discord.ui.View):
    def __init__(
        self,
        project_id: uuid.UUID,
        repository: ProductionRepository,
        settings: Settings,
        state: DashboardState | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self.project_id, self.repository, self.settings = project_id, repository, settings
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.custom_id = f"viralforge:guided:{project_id}:{(item.label or '').lower().replace(' ', '-')}"
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.custom_id = f"viralforge:project:{project_id}:{(item.label or '').lower().replace(' ', '-')}"
        if state is None:
            self._set_safe_defaults()
        else:
            self.apply_state(state)

    def _set_safe_defaults(self) -> None:
        for button in (
            self.accept_source_button,
            self.choose_candidate_button,
            self.candidate_list_button,
            self.source_url_button,
            self.reject_source_button,
            self.download,
            self.analyze,
            self.view_transcript,
            self.view_timeline,
            self.detect_opportunities,
            self.view_opportunities,
            self.generate,
            self.view_clips,
            self.approve_all_button,
        ):
            button.disabled = True

    def apply_state(self, state: DashboardState) -> None:
        project = state.project
        source_review = project.status == "SOURCE_REVIEW_REQUIRED"
        downloaded = bool(project.source_storage_key)
        analysis_ready = state.analysis is not None and state.analysis.status == "COMPLETED"
        self.accept_source_button.disabled = not source_review
        self.choose_candidate_button.disabled = not source_review
        self.candidate_list_button.disabled = not source_review
        self.source_url_button.disabled = state.source is None
        self.reject_source_button.disabled = not source_review
        self.download.disabled = downloaded or project.status not in {
            "SOURCE_RESOLVED",
            "SOURCE_ACCEPTED",
            "SOURCE_READY",
        }
        self.analyze.disabled = not downloaded or (
            state.analysis is not None
            and state.analysis.status in {"QUEUED", "RUNNING", "COMPLETED"}
        )
        self.view_transcript.disabled = state.transcript_segment_count == 0
        self.view_timeline.disabled = state.analysis_segment_count + state.analysis_event_count == 0
        self.detect_opportunities.disabled = not analysis_ready or bool(state.opportunity_count)
        self.view_opportunities.disabled = state.opportunity_count == 0
        self.generate.disabled = project.status != "SOURCE_READY" or state.total_clips > 0
        self.view_clips.disabled = state.total_clips == 0
        self.approve_all_button.disabled = (
            state.total_clips == 0 or state.approved + state.rejected >= state.total_clips
        )

    async def _authorize(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member) or not is_authorized(
            interaction.user, self.settings
        ):
            await interaction.response.send_message(
                unauthorized_message(), view=OperatorAccessHelpView(self.settings), ephemeral=True
            )
            return False
        return True

    async def refresh_dashboard(self, interaction: discord.Interaction) -> None:
        state = await asyncio.to_thread(self.repository.dashboard, self.project_id)
        self.apply_state(state)
        await interaction.message.edit(embed=dashboard_embed(state), view=self)  # type: ignore[union-attr]

    @discord.ui.button(label="Accept Source", style=discord.ButtonStyle.success)
    async def accept_source_button(
        self, interaction: discord.Interaction, _: discord.ui.Button["ProjectDashboardView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        try:
            await asyncio.to_thread(self.repository.accept_source, self.project_id)
        except ProductionError as error:
            await interaction.response.send_message(user_error(error), ephemeral=True)
            return
        await interaction.response.defer()
        await self.refresh_dashboard(interaction)

    @discord.ui.button(label="Choose Candidate", style=discord.ButtonStyle.secondary)
    async def choose_candidate_button(
        self, interaction: discord.Interaction, _: discord.ui.Button["ProjectDashboardView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        state = await asyncio.to_thread(self.repository.dashboard, self.project_id)
        candidates = await asyncio.to_thread(self.repository.sources, self.project_id)
        if not candidates:
            await interaction.response.send_message(
                "No source candidates are available yet. Refresh after source resolution completes.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=candidate_embed(candidates),
            view=CandidateReviewView(
                self.project_id,
                state.project.source_decision_version,
                candidates,
                self.repository,
                self.settings,
            ),
            ephemeral=True,
        )

    @discord.ui.button(label="View Candidate List", style=discord.ButtonStyle.secondary)
    async def candidate_list_button(
        self, interaction: discord.Interaction, _: discord.ui.Button["ProjectDashboardView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        candidates = await asyncio.to_thread(self.repository.sources, self.project_id)
        await interaction.response.send_message(embed=candidate_embed(candidates), ephemeral=True)

    @discord.ui.button(label="View Source URL", style=discord.ButtonStyle.secondary)
    async def source_url_button(
        self, interaction: discord.Interaction, _: discord.ui.Button["ProjectDashboardView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        state = await asyncio.to_thread(self.repository.dashboard, self.project_id)
        if state.source is None:
            await interaction.response.send_message(
                "No selected source is available.", ephemeral=True
            )
            return
        await interaction.response.send_message(state.source.source_url, ephemeral=True)

    @discord.ui.button(label="Reject Source", style=discord.ButtonStyle.danger)
    async def reject_source_button(
        self, interaction: discord.Interaction, _: discord.ui.Button["ProjectDashboardView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        try:
            await asyncio.to_thread(self.repository.reject_source, self.project_id)
        except ProductionError as error:
            await interaction.response.send_message(user_error(error), ephemeral=True)
            return
        await interaction.response.defer()
        await self.refresh_dashboard(interaction)

    @discord.ui.button(label="Download Video", style=discord.ButtonStyle.primary)
    async def download(
        self, interaction: discord.Interaction, _: discord.ui.Button["ProjectDashboardView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        await interaction.response.defer(thinking=True)
        try:
            await asyncio.to_thread(self.repository.download, self.project_id)
        except ProductionError as error:
            await interaction.followup.send(user_error(error), ephemeral=True)
            return
        await self.refresh_dashboard(interaction)
        await interaction.followup.send("Download completed.", ephemeral=True)

    @discord.ui.button(label="Start Real Analysis", style=discord.ButtonStyle.primary)
    async def analyze(
        self, interaction: discord.Interaction, _: discord.ui.Button["ProjectDashboardView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        try:
            await asyncio.to_thread(self.repository.request_analysis, self.project_id)
        except ProductionError as error:
            await interaction.response.send_message(user_error(error), ephemeral=True)
            return
        await interaction.response.send_message(
            "Analysis was queued once. Refresh for worker progress; no duplicate analysis was created.",
            ephemeral=True,
        )

    @discord.ui.button(label="View Transcript", style=discord.ButtonStyle.secondary)
    async def view_transcript(
        self, interaction: discord.Interaction, _: discord.ui.Button["ProjectDashboardView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        await AnalysisTextView.open(
            interaction, self.project_id, "transcript", self.repository, self.settings
        )

    @discord.ui.button(label="View Timeline", style=discord.ButtonStyle.secondary)
    async def view_timeline(
        self, interaction: discord.Interaction, _: discord.ui.Button["ProjectDashboardView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        await AnalysisTextView.open(
            interaction, self.project_id, "timeline", self.repository, self.settings
        )

    @discord.ui.button(label="Detect Opportunities", style=discord.ButtonStyle.primary)
    async def detect_opportunities(
        self, interaction: discord.Interaction, _: discord.ui.Button["ProjectDashboardView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        try:
            await asyncio.to_thread(self.repository.request_opportunities, self.project_id)
        except ProductionError as error:
            await interaction.response.send_message(user_error(error), ephemeral=True)
            return
        await interaction.response.send_message(
            "Opportunity detection queued from the stored analysis.", ephemeral=True
        )

    @discord.ui.button(label="View Opportunities", style=discord.ButtonStyle.secondary)
    async def view_opportunities(
        self, interaction: discord.Interaction, _: discord.ui.Button["ProjectDashboardView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        opportunities = await asyncio.to_thread(self.repository.opportunities, self.project_id)
        if not opportunities:
            await interaction.response.send_message(
                "No ranked clip opportunities are available yet.", ephemeral=True
            )
            return
        state = await asyncio.to_thread(self.repository.opportunity_state, opportunities[0].id)
        await interaction.response.send_message(
            embed=opportunity_embed(state),
            view=OpportunityReviewView(state, self.repository, self.settings),
            ephemeral=True,
        )

    @discord.ui.button(label="Generate Clips", style=discord.ButtonStyle.primary)
    async def generate(
        self, interaction: discord.Interaction, _: discord.ui.Button["ProjectDashboardView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        await interaction.response.defer(thinking=True)
        try:
            clips = await asyncio.to_thread(self.repository.generate, self.project_id)
        except ProductionError as error:
            await interaction.followup.send(user_error(error), ephemeral=True)
            return
        await self.refresh_dashboard(interaction)
        await interaction.followup.send(f"Generated {len(clips)} clips.", ephemeral=True)

    @discord.ui.button(label="View Clips", style=discord.ButtonStyle.secondary)
    async def view_clips(
        self, interaction: discord.Interaction, _: discord.ui.Button["ProjectDashboardView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        clips = await asyncio.to_thread(self.repository.clips, self.project_id)
        if not clips:
            await interaction.response.send_message("No clips are available yet.", ephemeral=True)
            return
        state = await asyncio.to_thread(self.repository.review_state, clips[0].id)
        issued = await asyncio.to_thread(self.repository.create_preview, state.clip.id)
        if issued.url:
            await interaction.response.send_message(
                content=f"Preview expires {issued.grant.expires_at.strftime('%Y-%m-%d %H:%M UTC')}.",
                embed=clip_embed(state.clip, state.total),
                view=ClipReviewView(state, self.repository, self.settings, issued.url),
            )
        else:
            await interaction.response.send_message(
                content="An active private preview exists. Select Refresh Preview Link to mint a replacement.",
                embed=clip_embed(state.clip, state.total),
                view=ClipReviewView(state, self.repository, self.settings),
            )
        message = await interaction.original_response()
        await asyncio.to_thread(self.repository.set_clip_message, state.clip.id, message.id)

    @discord.ui.button(label="Approve All", style=discord.ButtonStyle.success)
    async def approve_all_button(
        self, interaction: discord.Interaction, _: discord.ui.Button["ProjectDashboardView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        await interaction.response.defer(thinking=True)
        count = await asyncio.to_thread(self.repository.approve_all, self.project_id)
        await self.refresh_dashboard(interaction)
        await interaction.followup.send(f"Approved {count} rendered clips.", ephemeral=True)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary)
    async def refresh(
        self, interaction: discord.Interaction, _: discord.ui.Button["ProjectDashboardView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        await interaction.response.defer()
        await self.refresh_dashboard(interaction)

    @discord.ui.button(label="Home", style=discord.ButtonStyle.secondary)
    async def home(
        self, interaction: discord.Interaction, _: discord.ui.Button["ProjectDashboardView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        state = await asyncio.to_thread(self.repository.control_center)
        await interaction.response.edit_message(
            embed=control_center_embed(state),
            view=ControlCenterView(self.repository, self.settings, state),
        )


class AnalysisTextView(discord.ui.View):
    def __init__(
        self,
        project_id: uuid.UUID,
        kind: str,
        repository: ProductionRepository,
        settings: Settings,
        page: int = 0,
    ) -> None:
        super().__init__(timeout=300)
        self.project_id, self.kind, self.repository, self.settings, self.page = (
            project_id,
            kind,
            repository,
            settings,
            page,
        )

    async def _authorize(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member) or not is_authorized(
            interaction.user, self.settings
        ):
            await interaction.response.send_message(
                unauthorized_message(), view=OperatorAccessHelpView(self.settings), ephemeral=True
            )
            return False
        return True

    async def _embed(self) -> tuple[discord.Embed, int]:
        lines, total = await asyncio.to_thread(
            self.repository.analysis_text_page, self.project_id, self.kind, self.page
        )
        start = self.page * 8 + 1
        title = "Transcript" if self.kind == "transcript" else "Analysis timeline"
        embed = discord.Embed(title=title)
        embed.description = "\n".join(lines)[:4_000] or f"No {self.kind} entries are available."
        embed.set_footer(
            text=f"{min(start, total)}–{min(start + len(lines) - 1, total)} of {total}"
        )
        self.previous.disabled = self.page == 0
        self.next.disabled = (self.page + 1) * 8 >= total
        return embed, total

    @classmethod
    async def open(
        cls,
        interaction: discord.Interaction,
        project_id: uuid.UUID,
        kind: str,
        repository: ProductionRepository,
        settings: Settings,
    ) -> None:
        view = cls(project_id, kind, repository, settings)
        try:
            embed, _ = await view._embed()
        except ProductionError as error:
            await interaction.response.send_message(user_error(error), ephemeral=True)
            return
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous(
        self, interaction: discord.Interaction, _: discord.ui.Button["AnalysisTextView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        self.page = max(0, self.page - 1)
        embed, ignored_total = await self._embed()
        del ignored_total
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(
        self, interaction: discord.Interaction, _: discord.ui.Button["AnalysisTextView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        self.page += 1
        embed, total = await self._embed()
        if self.page * 8 >= total and self.page:
            self.page -= 1
            embed, ignored_total = await self._embed()
            del ignored_total
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Back to Project", style=discord.ButtonStyle.secondary)
    async def back_to_project(
        self, interaction: discord.Interaction, _: discord.ui.Button["AnalysisTextView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        state = await asyncio.to_thread(self.repository.dashboard, self.project_id)
        await interaction.response.edit_message(
            embed=dashboard_embed(state),
            view=ProjectDashboardView(self.project_id, self.repository, self.settings, state),
        )


def candidate_embed(candidates: list[ProductionSource]) -> discord.Embed:
    embed = discord.Embed(title="Source candidates")
    for number, candidate in enumerate(candidates[:25], start=1):
        value = f"{candidate.platform} · {candidate.uploader_name or 'Unknown'}\n{candidate.quality_score:.0f}/100 · {candidate.ownership_classification} · {candidate.watermark_status}\n{candidate.source_url}"
        embed.add_field(
            name=f"{number}. {candidate.video_title or 'Untitled'}"[:256],
            value=value[:1024],
            inline=False,
        )
    return embed


class CandidateChoiceSelect(discord.ui.Select["CandidateReviewView"]):
    def __init__(self, candidates: list[ProductionSource]) -> None:
        options = [
            discord.SelectOption(
                label=(candidate.video_title or candidate.uploader_name or "Untitled")[:100],
                description=f"{candidate.quality_score:.0f}/100 · {candidate.ownership_classification}"[
                    :100
                ],
                value=str(candidate.id),
            )
            for candidate in candidates[:25]
        ]
        super().__init__(
            placeholder="Choose a source candidate", min_values=1, max_values=1, options=options
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, CandidateReviewView):
            return
        if not await view._authorize(interaction):
            return
        try:
            await asyncio.to_thread(
                view.repository.choose_source,
                view.project_id,
                uuid.UUID(self.values[0]),
                view.expected_version,
            )
        except (ValueError, ProductionError) as error:
            code = (
                error.code if isinstance(error, ProductionError) else "SOURCE_CANDIDATE_NOT_FOUND"
            )
            await interaction.response.send_message(user_error(code), ephemeral=True)
            return
        await interaction.response.edit_message(
            content="Source candidate selected. Refresh the project dashboard.",
            embed=None,
            view=None,
        )


class CandidateReviewView(discord.ui.View):
    def __init__(
        self,
        project_id: uuid.UUID,
        expected_version: int,
        candidates: list[ProductionSource],
        repository: ProductionRepository,
        settings: Settings,
    ) -> None:
        super().__init__(timeout=300)
        self.project_id, self.expected_version, self.repository, self.settings = (
            project_id,
            expected_version,
            repository,
            settings,
        )
        self.add_item(CandidateChoiceSelect(candidates))

    async def _authorize(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member) or not is_authorized(
            interaction.user, self.settings
        ):
            await interaction.response.send_message(
                unauthorized_message(), view=OperatorAccessHelpView(self.settings), ephemeral=True
            )
            return False
        return True


def clip_embed(clip: ProductionClip, total: int) -> discord.Embed:
    embed = discord.Embed(title=f"Finished clip {clip.clip_number} of {total}")
    embed.description = (
        f"{clip.start_seconds:.1f}s–{clip.end_seconds:.1f}s ({clip.duration_seconds:.1f}s)"
    )
    embed.add_field(name="Timing", value=f"{clip.start_seconds:.1f}s to {clip.end_seconds:.1f}s")
    embed.add_field(
        name="Next step",
        value=(
            "Approve to prepare post details automatically."
            if clip.approval_status == "PENDING"
            else "This creative decision is already recorded."
        ),
    )
    if clip.caption:
        embed.add_field(name="Caption", value=clip.caption[:1024], inline=False)
    return embed


def opportunity_embed(state: OpportunityReviewState) -> discord.Embed:
    opportunity = state.opportunity
    embed = discord.Embed(title=f"Suggested clip {state.position + 1} of {state.total}")
    embed.description = (
        f"{opportunity.start_time:.1f}s–{opportunity.end_time:.1f}s "
        f"({opportunity.duration_seconds:.1f}s) · **{opportunity.overall_score:.1f}/100**"
    )
    embed.add_field(name="Confidence", value=f"{opportunity.confidence:.0%}", inline=True)
    embed.add_field(
        name="Next step",
        value="Use this clip to render it automatically, or skip it.",
        inline=True,
    )
    top_reasons = sorted(
        state.reasons, key=lambda reason: reason.score * reason.weight, reverse=True
    )[:3]
    embed.add_field(
        name="Why ViralForge suggested it",
        value="\n".join(f"• {reason.reason_type}: {reason.score:.0%}" for reason in top_reasons)
        or "No scored reasons.",
        inline=False,
    )
    embed.add_field(name="Why", value=opportunity.explanation[:1024], inline=False)
    embed.add_field(name="Transcript preview", value=state.transcript_preview[:1024], inline=False)
    return embed


def can_attach_preview(
    interaction: discord.Interaction, preview: bytes | None, settings: Settings
) -> bool:
    guild_limit = (
        interaction.guild.filesize_limit
        if interaction.guild is not None
        else settings.discord_max_preview_upload_bytes
    )
    return preview is not None and len(preview) <= min(
        settings.discord_max_preview_upload_bytes, guild_limit
    )


class CaptionModal(discord.ui.Modal, title="Edit caption"):
    caption: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
        label="Caption", max_length=2_000, required=True
    )

    def __init__(self, clip_id: uuid.UUID, repository: ProductionRepository) -> None:
        super().__init__()
        self.clip_id, self.repository = clip_id, repository

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            await asyncio.to_thread(self.repository.caption, self.clip_id, str(self.caption))
        except ProductionError as error:
            await interaction.response.send_message(user_error(error), ephemeral=True)
            return
        await interaction.response.send_message("Caption updated.", ephemeral=True)


def content_package_embed(
    package: ContentPackage, platform_field: str = "youtube_shorts_title"
) -> discord.Embed:
    fields = package.fields_json
    label = platform_field.replace("_", " ").title()
    embed = discord.Embed(title="Post details")
    embed.description = f"Status: **{package.status}** · confidence {package.confidence:.0%}"
    embed.add_field(
        name="Primary hook",
        value=str(fields.get("primary_hook", "Not generated"))[:1024],
        inline=False,
    )
    embed.add_field(
        name=label, value=str(fields.get(platform_field, "Not generated"))[:1024], inline=False
    )
    embed.add_field(
        name="Verified source facts",
        value="\n".join(package.verified_facts_json)[:1024] or "None",
        inline=False,
    )
    embed.add_field(
        name="Transcript-derived statements",
        value="\n".join(package.transcript_statements_json)[:1024] or "None",
        inline=False,
    )
    embed.add_field(
        name="Uncertainty",
        value="\n".join(package.uncertainty_json)[:1024] or "Review required.",
        inline=False,
    )
    return embed


class ContentPackageEditModal(discord.ui.Modal):
    value: discord.ui.TextInput[discord.ui.Modal]

    def __init__(
        self,
        package: ContentPackage,
        field_name: str,
        repository: ProductionRepository,
    ) -> None:
        super().__init__(title=f"Edit {field_name.replace('_', ' ').title()}")
        self.package_id = package.id
        self.expected_version = package.review_version
        self.field_name = field_name
        self.repository = repository
        self.value = discord.ui.TextInput(
            label=field_name.replace("_", " ").title(),
            default=str(package.fields_json.get(field_name, ""))[:4000],
            style=discord.TextStyle.paragraph,
            max_length=4000,
        )
        self.add_item(self.value)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        package = await asyncio.to_thread(self.repository.content_package, self.package_id)
        if package is None:
            await interaction.response.send_message(
                "Content package no longer exists.", ephemeral=True
            )
            return
        fields = dict(package.fields_json)
        fields[self.field_name] = str(self.value)
        try:
            updated = await asyncio.to_thread(
                self.repository.edit_content_package,
                package.id,
                self.expected_version,
                fields,
            )
        except ProductionError as error:
            await interaction.response.send_message(user_error(error), ephemeral=True)
            return
        await interaction.response.send_message(
            f"Content package updated (review version {updated.review_version}).", ephemeral=True
        )


class ContentPackagePlatformSelect(discord.ui.Select["ContentPackageReviewView"]):
    def __init__(self, package_id: uuid.UUID, selected: str) -> None:
        options = [
            ("YouTube Shorts", "youtube_shorts_title"),
            ("TikTok", "tiktok_caption"),
            ("Instagram", "instagram_caption"),
            ("Facebook", "facebook_caption"),
            ("X", "x_post"),
            ("Description", "description"),
        ]
        super().__init__(
            placeholder="Select an editable platform suggestion",
            custom_id=f"viralforge:content-package:{package_id}:platform",
            options=[
                discord.SelectOption(label=label, value=value, default=value == selected)
                for label, value in options
            ],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        assert isinstance(view, ContentPackageReviewView)
        view.platform_field = self.values[0]
        package = await asyncio.to_thread(view.repository.content_package, view.package_id)
        if package is None:
            await interaction.response.send_message(
                "Content package no longer exists.", ephemeral=True
            )
            return
        await interaction.response.edit_message(
            embed=content_package_embed(package, view.platform_field), view=view
        )


class ContentPackageReviewView(discord.ui.View):
    def __init__(
        self, package: ContentPackage, repository: ProductionRepository, settings: Settings
    ) -> None:
        super().__init__(timeout=600)
        self.package_id, self.repository, self.settings = package.id, repository, settings
        self.platform_field = "youtube_shorts_title"
        self.add_item(ContentPackagePlatformSelect(package.id, self.platform_field))
        self.approve_button.disabled = package.status != ContentPackageStatus.PENDING
        self.reject_button.disabled = package.status != ContentPackageStatus.PENDING

    async def _authorized(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member) or not is_authorized(
            interaction.user, self.settings
        ):
            await interaction.response.send_message(
                unauthorized_message(), view=OperatorAccessHelpView(self.settings), ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Edit selected", style=discord.ButtonStyle.secondary, row=1)
    async def edit_selected(
        self, interaction: discord.Interaction, _: discord.ui.Button["ContentPackageReviewView"]
    ) -> None:
        if not await self._authorized(interaction):
            return
        package = await asyncio.to_thread(self.repository.content_package, self.package_id)
        if package is None:
            await interaction.response.send_message(
                "Content package no longer exists.", ephemeral=True
            )
            return
        await interaction.response.send_modal(
            ContentPackageEditModal(package, self.platform_field, self.repository)
        )

    @discord.ui.button(label="Approve package", style=discord.ButtonStyle.success, row=1)
    async def approve_button(
        self, interaction: discord.Interaction, _: discord.ui.Button["ContentPackageReviewView"]
    ) -> None:
        if not await self._authorized(interaction):
            return
        package = await asyncio.to_thread(self.repository.content_package, self.package_id)
        if package is None:
            await interaction.response.send_message(
                "Content package no longer exists.", ephemeral=True
            )
            return
        try:
            package = await asyncio.to_thread(
                self.repository.decide_content_package, package.id, package.review_version, True
            )
        except ProductionError as error:
            await interaction.response.send_message(user_error(error), ephemeral=True)
            return
        self.approve_button.disabled = True
        self.reject_button.disabled = True
        await interaction.response.edit_message(
            embed=content_package_embed(package, self.platform_field), view=self
        )

    @discord.ui.button(label="Reject package", style=discord.ButtonStyle.danger, row=1)
    async def reject_button(
        self, interaction: discord.Interaction, _: discord.ui.Button["ContentPackageReviewView"]
    ) -> None:
        if not await self._authorized(interaction):
            return
        package = await asyncio.to_thread(self.repository.content_package, self.package_id)
        if package is None:
            await interaction.response.send_message(
                "Content package no longer exists.", ephemeral=True
            )
            return
        try:
            package = await asyncio.to_thread(
                self.repository.decide_content_package, package.id, package.review_version, False
            )
        except ProductionError as error:
            await interaction.response.send_message(user_error(error), ephemeral=True)
            return
        self.approve_button.disabled = True
        self.reject_button.disabled = True
        await interaction.response.edit_message(
            embed=content_package_embed(package, self.platform_field), view=self
        )


class OpportunityReviewView(discord.ui.View):
    def __init__(
        self, state: OpportunityReviewState, repository: ProductionRepository, settings: Settings
    ) -> None:
        super().__init__(timeout=None)
        self.state, self.repository, self.settings = state, repository, settings
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.custom_id = (
                    f"viralforge:opportunity:{state.opportunity.id}:"
                    f"{(item.label or '').lower().replace(' ', '-')}"
                )
        self.previous.disabled = state.position == 0
        self.next.disabled = state.position == state.total - 1

    async def _authorized(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member) or not is_authorized(
            interaction.user, self.settings
        ):
            await interaction.response.send_message(
                unauthorized_message(), view=OperatorAccessHelpView(self.settings), ephemeral=True
            )
            return False
        return True

    async def _refresh_review(self, interaction: discord.Interaction) -> None:
        self.previous.disabled = self.state.position == 0
        self.next.disabled = self.state.position == self.state.total - 1
        await interaction.response.edit_message(embed=opportunity_embed(self.state), view=self)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous(
        self, interaction: discord.Interaction, _: discord.ui.Button["OpportunityReviewView"]
    ) -> None:
        if not await self._authorized(interaction):
            return
        try:
            self.state = await asyncio.to_thread(
                self.repository.opportunity_state, self.state.opportunity.id, -1
            )
        except ProductionError as error:
            await interaction.response.send_message(user_error(error), ephemeral=True)
            return
        await self._refresh_review(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(
        self, interaction: discord.Interaction, _: discord.ui.Button["OpportunityReviewView"]
    ) -> None:
        if not await self._authorized(interaction):
            return
        try:
            self.state = await asyncio.to_thread(
                self.repository.opportunity_state, self.state.opportunity.id, 1
            )
        except ProductionError as error:
            await interaction.response.send_message(user_error(error), ephemeral=True)
            return
        await self._refresh_review(interaction)

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
    async def approve(
        self, interaction: discord.Interaction, _: discord.ui.Button["OpportunityReviewView"]
    ) -> None:
        if not await self._authorized(interaction):
            return
        # Defer the component update itself. Using a thinking response here
        # creates a second ephemeral card instead of updating the review card
        # the operator actually clicked.
        await interaction.response.defer()
        try:
            opportunity = await asyncio.to_thread(
                self.repository.decide_opportunity, self.state.opportunity.id, True
            )
            state = await asyncio.to_thread(self.repository.dashboard, opportunity.project_id)
        except ProductionError as error:
            await interaction.followup.send(user_error(error), ephemeral=True)
            return
        await interaction.edit_original_response(
            content="Clip approved. Rendering has started automatically; refresh this card for progress.",
            embed=guided_project_embed(state),
            view=GuidedProjectView(opportunity.project_id, self.repository, self.settings),
        )

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger)
    async def reject(
        self, interaction: discord.Interaction, _: discord.ui.Button["OpportunityReviewView"]
    ) -> None:
        if not await self._authorized(interaction):
            return
        # Decision writes can take longer than Discord's interaction window.
        # Acknowledge first so rejecting a suggestion is as reliable as approval.
        await interaction.response.defer()
        try:
            opportunity = await asyncio.to_thread(
                self.repository.decide_opportunity, self.state.opportunity.id, False
            )
        except ProductionError as error:
            await interaction.followup.send(user_error(error), ephemeral=True)
            return
        next_pending = await asyncio.to_thread(
            self.repository.first_pending_opportunity_for_project, opportunity.project_id
        )
        if next_pending is not None:
            self.state = next_pending
            self.previous.disabled = self.state.position == 0
            self.next.disabled = self.state.position == self.state.total - 1
            await interaction.edit_original_response(embed=opportunity_embed(self.state), view=self)
            return
        state = await asyncio.to_thread(self.repository.dashboard, opportunity.project_id)
        await interaction.edit_original_response(
            content="All suggested clips were declined. No clips will be rendered.",
            embed=guided_project_embed(state),
            view=GuidedProjectView(opportunity.project_id, self.repository, self.settings),
        )

    @discord.ui.button(label="View Details", style=discord.ButtonStyle.secondary)
    async def details(
        self, interaction: discord.Interaction, _: discord.ui.Button["OpportunityReviewView"]
    ) -> None:
        if not await self._authorized(interaction):
            return
        await interaction.response.send_message(
            self.state.opportunity.explanation,
            ephemeral=True,
        )

    @discord.ui.button(label="Back to Project", style=discord.ButtonStyle.secondary)
    async def back_to_project(
        self, interaction: discord.Interaction, _: discord.ui.Button["OpportunityReviewView"]
    ) -> None:
        if not await self._authorized(interaction):
            return
        state = await asyncio.to_thread(
            self.repository.dashboard, self.state.opportunity.project_id
        )
        await interaction.response.edit_message(
            embed=dashboard_embed(state),
            view=ProjectDashboardView(state.project.id, self.repository, self.settings, state),
        )


class ClipReviewView(discord.ui.View):
    def __init__(
        self,
        state: ReviewState,
        repository: ProductionRepository,
        settings: Settings,
        preview_url: str | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self.state, self.repository, self.settings = state, repository, settings
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.custom_id = f"viralforge:clip:{state.clip.id}:{(item.label or '').lower().replace(' ', '-')}"
        self.previous.disabled = state.position == 0
        self.next.disabled = state.position == state.total - 1
        if preview_url:
            self.add_item(
                discord.ui.Button(
                    label="Open Preview", style=discord.ButtonStyle.link, url=preview_url
                )
            )

    async def _authorized(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member) or not is_authorized(
            interaction.user, self.settings
        ):
            await interaction.response.send_message(
                unauthorized_message(), view=OperatorAccessHelpView(self.settings), ephemeral=True
            )
            return False
        return True

    @discord.ui.button(
        label="Approve", style=discord.ButtonStyle.success, custom_id="viralforge:clip:approve"
    )
    async def approve(
        self, interaction: discord.Interaction, _: discord.ui.Button["ClipReviewView"]
    ) -> None:
        if not await self._authorized(interaction):
            return
        try:
            clip = await asyncio.to_thread(self.repository.decide, self.state.clip.id, True)
            self.state = await asyncio.to_thread(self.repository.review_state, clip.id)
        except ProductionError as error:
            await interaction.response.send_message(user_error(error), ephemeral=True)
            return
        await self._edit_review(interaction)

    @discord.ui.button(
        label="Reject", style=discord.ButtonStyle.danger, custom_id="viralforge:clip:reject"
    )
    async def reject(
        self, interaction: discord.Interaction, _: discord.ui.Button["ClipReviewView"]
    ) -> None:
        if not await self._authorized(interaction):
            return
        try:
            clip = await asyncio.to_thread(self.repository.decide, self.state.clip.id, False)
            self.state = await asyncio.to_thread(self.repository.review_state, clip.id)
        except ProductionError as error:
            await interaction.response.send_message(user_error(error), ephemeral=True)
            return
        await self._edit_review(interaction)

    @discord.ui.button(
        label="Edit Caption",
        style=discord.ButtonStyle.secondary,
        custom_id="viralforge:clip:caption",
    )
    async def edit_caption(
        self, interaction: discord.Interaction, _: discord.ui.Button["ClipReviewView"]
    ) -> None:
        if not await self._authorized(interaction):
            return
        await interaction.response.send_modal(CaptionModal(self.state.clip.id, self.repository))

    @discord.ui.button(
        label="Content Package",
        style=discord.ButtonStyle.primary,
        custom_id="viralforge:clip:content-package",
    )
    async def content_package(
        self, interaction: discord.Interaction, _: discord.ui.Button["ClipReviewView"]
    ) -> None:
        if not await self._authorized(interaction):
            return
        try:
            package = await asyncio.to_thread(
                self.repository.request_content_package, self.state.clip.id
            )
        except ProductionError as error:
            await interaction.response.send_message(user_error(error), ephemeral=True)
            return
        if package.status == ContentPackageStatus.QUEUED:
            from app.worker import generate_content_package

            generate_content_package.delay(str(self.state.clip.id))
            await interaction.response.send_message(
                "Content package generation was queued. Select Content Package again after the worker completes; this does not publish or queue the clip.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=content_package_embed(package),
            view=ContentPackageReviewView(package, self.repository, self.settings),
            ephemeral=True,
        )

    async def _edit_review(self, interaction: discord.Interaction) -> None:
        self.previous.disabled = self.state.position == 0
        self.next.disabled = self.state.position == self.state.total - 1
        await interaction.response.edit_message(
            content="Use Refresh Preview Link for a new private browser-preview URL.",
            embed=clip_embed(self.state.clip, self.state.total),
            attachments=[],
            view=self,
        )

    @discord.ui.button(
        label="Refresh Preview Link",
        style=discord.ButtonStyle.secondary,
        custom_id="viralforge:clip:refresh-preview",
    )
    async def refresh_preview(
        self, interaction: discord.Interaction, _: discord.ui.Button["ClipReviewView"]
    ) -> None:
        if not await self._authorized(interaction):
            return
        try:
            issued = await asyncio.to_thread(
                self.repository.create_preview, self.state.clip.id, True
            )
        except ProductionError as error:
            await interaction.response.send_message(user_error(error), ephemeral=True)
            return
        view = discord.ui.View(timeout=600)
        view.add_item(
            discord.ui.Button(label="Open Preview", style=discord.ButtonStyle.link, url=issued.url)
        )
        await interaction.response.send_message(
            f"Private preview link refreshed. Expires {issued.grant.expires_at.strftime('%Y-%m-%d %H:%M UTC')}. It is not stored as a raw token.",
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(
        label="Previous", style=discord.ButtonStyle.secondary, custom_id="viralforge:clip:previous"
    )
    async def previous(
        self, interaction: discord.Interaction, _: discord.ui.Button["ClipReviewView"]
    ) -> None:
        if not await self._authorized(interaction):
            return
        try:
            self.state = await asyncio.to_thread(
                self.repository.review_state, self.state.clip.id, -1
            )
        except ProductionError as error:
            await interaction.response.send_message(user_error(error), ephemeral=True)
            return
        await self._edit_review(interaction)

    @discord.ui.button(
        label="Next", style=discord.ButtonStyle.secondary, custom_id="viralforge:clip:next"
    )
    async def next(
        self, interaction: discord.Interaction, _: discord.ui.Button["ClipReviewView"]
    ) -> None:
        if not await self._authorized(interaction):
            return
        try:
            self.state = await asyncio.to_thread(
                self.repository.review_state, self.state.clip.id, 1
            )
        except ProductionError as error:
            await interaction.response.send_message(user_error(error), ephemeral=True)
            return
        await self._edit_review(interaction)

    @discord.ui.button(
        label="Back to Project",
        style=discord.ButtonStyle.secondary,
        custom_id="viralforge:clip:project",
    )
    async def back_to_project(
        self, interaction: discord.Interaction, _: discord.ui.Button["ClipReviewView"]
    ) -> None:
        if not await self._authorized(interaction):
            return
        state = await asyncio.to_thread(self.repository.dashboard, self.state.project_id)
        await interaction.response.edit_message(
            content=None,
            embed=dashboard_embed(state),
            attachments=[],
            view=ProjectDashboardView(self.state.project_id, self.repository, self.settings, state),
        )


def publish_confirmation_embed(request: PublishRequest) -> discord.Embed:
    embed = discord.Embed(title="Confirm YouTube publishing")
    embed.description = (
        "This action is explicit and review-gated. No upload starts until Confirm is selected."
    )
    embed.add_field(name="Decision", value=request.decision_type)
    embed.add_field(name="Status", value=request.status)
    embed.add_field(
        name="Schedule",
        value=request.scheduled_for or "Publish manually after confirmation",
        inline=False,
    )
    embed.add_field(
        name="Privacy",
        value=str(request.platform_metadata.get("privacyStatus", "unlisted")).upper(),
    )
    return embed


class PublishConfirmationView(discord.ui.View):
    def __init__(
        self, request_id: uuid.UUID, repository: ProductionRepository, settings: Settings
    ) -> None:
        super().__init__(timeout=600)
        self.request_id, self.repository, self.settings = request_id, repository, settings

    async def _authorized(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member) or not is_authorized(
            interaction.user, self.settings
        ):
            await interaction.response.send_message(
                unauthorized_message(), view=OperatorAccessHelpView(self.settings), ephemeral=True
            )
            return False
        return True

    @discord.ui.button(
        label="Confirm", style=discord.ButtonStyle.danger, custom_id="viralforge:publish:confirm"
    )
    async def confirm(
        self, interaction: discord.Interaction, _: discord.ui.Button["PublishConfirmationView"]
    ) -> None:
        if not await self._authorized(interaction):
            return
        try:
            request = await asyncio.to_thread(
                self.repository.confirm_publish_request, self.request_id
            )
        except (ProductionError, PublishingError) as error:
            await interaction.response.send_message(user_error(error), ephemeral=True)
            return
        await interaction.response.edit_message(
            content="Publishing decision confirmed.",
            embed=publish_confirmation_embed(request),
            view=None,
        )

    @discord.ui.button(
        label="Cancel", style=discord.ButtonStyle.secondary, custom_id="viralforge:publish:cancel"
    )
    async def cancel(
        self, interaction: discord.Interaction, _: discord.ui.Button["PublishConfirmationView"]
    ) -> None:
        if not await self._authorized(interaction):
            return
        try:
            request = await asyncio.to_thread(
                self.repository.cancel_publish_request, self.request_id
            )
        except (ProductionError, PublishingError) as error:
            await interaction.response.send_message(user_error(error), ephemeral=True)
            return
        await interaction.response.edit_message(
            content="Publishing request cancelled before upload.",
            embed=publish_confirmation_embed(request),
            view=None,
        )


def discovery_embed(media: DiscoveredMedia) -> discord.Embed:
    embed = discord.Embed(
        title=media.title or "Recommended video",
        description="A public video ViralForge found for your review.",
    )
    embed.add_field(name="Source", value=media.uploader or "Unknown", inline=True)
    embed.add_field(name="Match", value=f"{media.discovery_score:.0f}/100", inline=True)
    embed.add_field(name="Original link", value=media.canonical_url, inline=False)
    reason = media.metadata_json.get("discovery_reason")
    if reason:
        embed.add_field(name="Discovery reason", value=str(reason)[:1024], inline=False)
    return embed


class DiscoveryReviewView(discord.ui.View):
    def __init__(
        self, media: DiscoveredMedia, repository: DiscoveryRepository, settings: Settings
    ) -> None:
        super().__init__(timeout=None)
        self.media_id, self.version, self.repository, self.settings = (
            media.id,
            media.review_version,
            repository,
            settings,
        )
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.custom_id = f"viralforge:discovery:{media.id}:{(item.label or '').lower().replace(' ', '-')}"

    async def _authorize(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member) or not is_authorized(
            interaction.user, self.settings
        ):
            await interaction.response.send_message(
                unauthorized_message(), view=OperatorAccessHelpView(self.settings), ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Approve for Processing", style=discord.ButtonStyle.success)
    async def approve(
        self, interaction: discord.Interaction, _: discord.ui.Button["DiscoveryReviewView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        try:
            media = await asyncio.to_thread(self.repository.approve, self.media_id, self.version)
        except ProductionError as error:
            await interaction.response.send_message(user_error(error), ephemeral=True)
            return
        self.version = media.review_version
        await interaction.response.edit_message(embed=discovery_embed(media), view=self)

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger)
    async def reject(
        self, interaction: discord.Interaction, _: discord.ui.Button["DiscoveryReviewView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        try:
            media = await asyncio.to_thread(self.repository.reject, self.media_id, self.version)
        except ProductionError as error:
            await interaction.response.send_message(user_error(error), ephemeral=True)
            return
        self.version = media.review_version
        await interaction.response.edit_message(embed=discovery_embed(media), view=self)

    @discord.ui.button(label="View Source", style=discord.ButtonStyle.secondary)
    async def source(
        self, interaction: discord.Interaction, _: discord.ui.Button["DiscoveryReviewView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        media = await asyncio.to_thread(self.repository.media, self.media_id)
        await interaction.response.send_message(media.canonical_url, ephemeral=True)

    @discord.ui.button(label="Home", style=discord.ButtonStyle.secondary)
    async def home(
        self, interaction: discord.Interaction, _: discord.ui.Button["DiscoveryReviewView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        state = await asyncio.to_thread(self.repository.control_center)
        await interaction.response.edit_message(
            embed=control_center_embed(state),
            view=ControlCenterView(self.repository, self.settings, state),
        )


def control_center_embed(state: ControlCenterState) -> discord.Embed:
    """Default operator screen: plain-language decisions, not internal state."""
    embed = discord.Embed(
        title="ViralForge",
        description=(
            f"**Active brand: {state.active_brand_name}**\n"
            f"{operator_attention_summary(state)}"
        ),
    )
    embed.add_field(
        name="Needs attention",
        value=(
            f"{state.source_review_count} video{'s' if state.source_review_count != 1 else ''} to review\n"
            f"{state.opportunity_pending_count} suggested clip{'s' if state.opportunity_pending_count != 1 else ''} to choose\n"
            f"{state.clip_pending_count} finished clip{'s' if state.clip_pending_count != 1 else ''} to approve\n"
            f"{state.failure_count} issue{'s' if state.failure_count != 1 else ''} to check"
        ),
        inline=True,
    )
    embed.add_field(
        name="Today",
        value=(
            f"Videos in progress: {state.total_projects}\n"
            f"Videos being prepared: {state.analysis_queued_count + state.analysis_running_count}\n"
            f"Content ready: {state.queue_ready_count}"
        ),
        inline=True,
    )
    embed.add_field(
        name="How ViralForge is helping",
        value="Accepted videos are prepared automatically and pause only for your review.",
        inline=False,
    )
    return embed


def operator_attention_summary(state: ControlCenterState) -> str:
    if state.failure_count:
        return "A recent item needs a quick check before continuing."
    if state.source_review_count or state.discovery_pending_count:
        return "New videos are ready for your decision."
    if state.opportunity_pending_count:
        return "Suggested clips are ready for your decision."
    if state.clip_pending_count:
        return "Finished clips are ready for your approval."
    if state.queue_ready_count:
        return "Content is ready. Choose when to post it."
    if state.analysis_queued_count or state.analysis_running_count:
        return "ViralForge is preparing your video. You do not need to do anything yet."
    return "Everything is caught up. Add a video or find a new one to continue."


def ready_to_post_embed(
    items: list[tuple[PostingQueueItem, ProductionClip, ProductionProject]], settings: Settings
) -> discord.Embed:
    embed = discord.Embed(title="Content ready")
    if not items:
        embed.description = "No finished content is waiting right now."
        return embed
    embed.description = (
        "Your content is ready for the next publishing decision."
        if settings.publishing_enabled
        else "Your content is ready, but no publishing account is connected yet."
    )
    for item, clip, project in items[:10]:
        destination = item.target_account_id or "No destination connected"
        embed.add_field(
            name=(project.source_title or "Finished video")[:256],
            value=(
                f"Clip {clip.clip_number} · {clip.duration_seconds:.0f}s\n"
                f"Destination: {destination}\n"
                f"{'Ready for explicit publishing' if settings.publishing_enabled else 'Save as content-ready'}"
            )[:1024],
            inline=False,
        )
    return embed


def projects_embed(projects: list[ProductionProject], title: str, empty: str) -> discord.Embed:
    embed = discord.Embed(title=title)
    if not projects:
        embed.description = empty
        return embed
    for project in projects[:25]:
        source = (project.source_title or project.source_url)[:180]
        embed.add_field(
            name=source,
            value=f"Status: **{project.status}** · ID: `{project.id}`",
            inline=False,
        )
    return embed


class BrandPicker(discord.ui.Select["BrandSelectionView"]):
    def __init__(self, brands: list[Brand]) -> None:
        super().__init__(
            placeholder="Select the active ViralForge brand",
            options=[
                discord.SelectOption(label=brand.name[:100], value=str(brand.id))
                for brand in brands[:25]
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, BrandSelectionView) or not await view._authorize(interaction):
            return
        brand = await asyncio.to_thread(view.repository.select_brand, uuid.UUID(self.values[0]))
        state = await asyncio.to_thread(view.repository.control_center)
        await interaction.response.edit_message(
            content=f"Active brand: **{brand.name}**",
            embed=control_center_embed(state),
            view=OperatorHomeView(view.repository, view.settings),
        )


class BrandSelectionView(discord.ui.View):
    def __init__(
        self, brands: list[Brand], repository: ProductionRepository, settings: Settings
    ) -> None:
        super().__init__(timeout=300)
        self.repository, self.settings = repository, settings
        if brands:
            self.add_item(BrandPicker(brands))

    async def _authorize(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member) or not is_authorized(
            interaction.user, self.settings
        ):
            await interaction.response.send_message(
                unauthorized_message(), view=OperatorAccessHelpView(self.settings), ephemeral=True
            )
            return False
        return True


class ProjectPicker(discord.ui.Select["ProjectListView"]):
    def __init__(self, projects: list[ProductionProject]) -> None:
        super().__init__(
            placeholder="Open a project dashboard",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=(project.source_title or "Production project")[:100],
                    description=project.status[:100],
                    value=str(project.id),
                )
                for project in projects[:25]
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, ProjectListView) or not await view._authorize(interaction):
            return
        try:
            state = await asyncio.to_thread(view.repository.dashboard, uuid.UUID(self.values[0]))
        except (ValueError, ProductionError) as error:
            await interaction.response.send_message(
                user_error(error if isinstance(error, ProductionError) else "PROJECT_NOT_FOUND"),
                ephemeral=True,
            )
            return
        await interaction.response.edit_message(
            embed=guided_project_embed(state),
            view=GuidedProjectView(state.project.id, view.repository, view.settings),
        )


class ProjectListView(discord.ui.View):
    def __init__(
        self,
        projects: list[ProductionProject],
        repository: ProductionRepository,
        settings: Settings,
    ) -> None:
        super().__init__(timeout=300)
        self.repository, self.settings = repository, settings
        if projects:
            self.add_item(ProjectPicker(projects))

    async def _authorize(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member) or not is_authorized(
            interaction.user, self.settings
        ):
            await interaction.response.send_message(
                unauthorized_message(), view=OperatorAccessHelpView(self.settings), ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Home", style=discord.ButtonStyle.secondary)
    async def home(
        self, interaction: discord.Interaction, _: discord.ui.Button["ProjectListView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        state = await asyncio.to_thread(self.repository.control_center)
        await interaction.response.edit_message(
            embed=control_center_embed(state),
            view=ControlCenterView(self.repository, self.settings, state),
        )


class ControlCenterView(discord.ui.View):
    def __init__(
        self,
        repository: ProductionRepository,
        settings: Settings,
        state: ControlCenterState | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self.repository, self.settings, self.state = repository, settings, state

    async def _authorize(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member) or not is_authorized(
            interaction.user, self.settings
        ):
            await interaction.response.send_message(
                unauthorized_message(), view=OperatorAccessHelpView(self.settings), ephemeral=True
            )
            return False
        return True

    @discord.ui.button(
        label="Discovery",
        style=discord.ButtonStyle.secondary,
        custom_id="viralforge:home:discovery",
    )
    async def discovery(
        self, interaction: discord.Interaction, _: discord.ui.Button["ControlCenterView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        items = await asyncio.to_thread(DiscoveryRepository(self.settings).discovery_queue)
        if not items:
            await interaction.response.send_message(
                "Discovery is clear: no items need review.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=discovery_embed(items[0]),
            view=DiscoveryReviewView(items[0], DiscoveryRepository(self.settings), self.settings),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Projects", style=discord.ButtonStyle.primary, custom_id="viralforge:home:projects"
    )
    async def projects(
        self, interaction: discord.Interaction, _: discord.ui.Button["ControlCenterView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        projects = await asyncio.to_thread(self.repository.projects)
        await interaction.response.send_message(
            embed=projects_embed(
                projects, "Production projects", "No production projects have been submitted."
            ),
            view=ProjectListView(projects, self.repository, self.settings),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Source Review",
        style=discord.ButtonStyle.primary,
        custom_id="viralforge:home:source-review",
    )
    async def source_review(
        self, interaction: discord.Interaction, _: discord.ui.Button["ControlCenterView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        projects = await asyncio.to_thread(self.repository.projects, "SOURCE_REVIEW_REQUIRED")
        await interaction.response.send_message(
            embed=projects_embed(
                projects, "Source review", "No projects are awaiting source review."
            ),
            view=ProjectListView(projects, self.repository, self.settings),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Analysis", style=discord.ButtonStyle.secondary, custom_id="viralforge:home:analysis"
    )
    async def analysis(
        self, interaction: discord.Interaction, _: discord.ui.Button["ControlCenterView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        state = await asyncio.to_thread(self.repository.control_center)
        await interaction.response.send_message(embed=control_center_embed(state), ephemeral=True)

    @discord.ui.button(
        label="Opportunities",
        style=discord.ButtonStyle.primary,
        custom_id="viralforge:home:opportunities",
    )
    async def opportunities(
        self, interaction: discord.Interaction, _: discord.ui.Button["ControlCenterView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        state = await asyncio.to_thread(self.repository.first_pending_opportunity)
        if state is None:
            await interaction.response.send_message(
                "No clip opportunities are waiting for review.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=opportunity_embed(state),
            view=OpportunityReviewView(state, self.repository, self.settings),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Clips", style=discord.ButtonStyle.primary, custom_id="viralforge:home:clips"
    )
    async def clips(
        self, interaction: discord.Interaction, _: discord.ui.Button["ControlCenterView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        state = await asyncio.to_thread(self.repository.first_pending_clip)
        if state is None:
            await interaction.response.send_message(
                "No rendered clips are waiting for review.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=clip_embed(state.clip, state.total),
            view=ClipReviewView(state, self.repository, self.settings),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Queue", style=discord.ButtonStyle.secondary, custom_id="viralforge:home:queue"
    )
    async def queue(
        self, interaction: discord.Interaction, _: discord.ui.Button["ControlCenterView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        items = await asyncio.to_thread(self.repository.queue)
        body = (
            "\n".join(
                f"• {project.source_title or 'Project'} — clip {clip.clip_number}: {item.caption or 'No caption'}"
                for item, clip, project in items
            )
            or "No clips are ready to post."
        )
        await interaction.response.send_message(body[:2_000], ephemeral=True)

    @discord.ui.button(
        label="System Status",
        style=discord.ButtonStyle.secondary,
        custom_id="viralforge:home:status",
    )
    async def system_status(
        self, interaction: discord.Interaction, _: discord.ui.Button["ControlCenterView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        status = operational_status(self.settings)
        await interaction.response.send_message(
            "\n".join(
                f"{name}: {'ready' if ready else 'not configured'}"
                for name, ready in status.items()
            ),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Refresh", style=discord.ButtonStyle.success, custom_id="viralforge:home:refresh"
    )
    async def refresh(
        self, interaction: discord.Interaction, _: discord.ui.Button["ControlCenterView"]
    ) -> None:
        if not await self._authorize(interaction):
            return
        self.state = await asyncio.to_thread(self.repository.control_center)
        await interaction.response.edit_message(embed=control_center_embed(self.state), view=self)


def guided_project_embed(state: DashboardState) -> discord.Embed:
    """A customer-facing project timeline; technical diagnostics stay in More Details."""
    project = state.project
    source_name = project.source_title or "Your selected video"
    if project.status == "SOURCE_REVIEW_REQUIRED":
        progress, next_step = "Source → Review", "Choose whether to use this video."
    elif project.status == "DOWNLOADING":
        progress, next_step = "Source → Downloading", "ViralForge is securely downloading this video."
    elif not project.source_storage_key:
        progress, next_step = "Source → Preparing", "ViralForge is preparing this video automatically."
    elif state.analysis is None or state.analysis.status in {"QUEUED", "RUNNING"}:
        progress, next_step = "Source → Preparing video", "ViralForge is reviewing the video and finding moments."
    elif not state.opportunity_count:
        progress, next_step = "Source → Finding moments", "ViralForge is preparing clip suggestions."
    elif state.pending_opportunity_count:
        progress, next_step = "Source → Suggested clips", "Choose the clip you want to use next."
    elif state.approved_opportunity_count and not state.total_clips:
        progress, next_step = "Source → Rendering", "ViralForge is creating your approved clip automatically."
    elif state.opportunity_count and not state.total_clips:
        progress, next_step = "Source → Suggestions reviewed", "No clips were selected. Choose another video when ready."
    elif state.total_clips and state.approved < state.total_clips:
        progress, next_step = "Source → Clip ready", "Review the finished clip before it becomes content-ready."
    else:
        progress, next_step = "Source → Content ready", "Review the posting decision when you are ready."
    embed = discord.Embed(title=source_name[:256], description=f"**{progress}**\n{next_step}")
    embed.add_field(name="Original video", value=project.source_url[:1024], inline=False)
    embed.add_field(
        name="Video details",
        value=(
            f"{project.source_duration_seconds:.0f} seconds"
            if project.source_duration_seconds
            else "Duration will appear when the video is ready."
        ),
        inline=True,
    )
    embed.add_field(
        name="Progress",
        value=(
            f"Suggested clips: {state.pending_opportunity_count}\n"
            f"Finished clips: {state.total_clips}\n"
            f"Content ready: {state.queued}"
        ),
        inline=True,
    )
    if project.status == "DOWNLOADING":
        percent = max(0, min(100, project.download_progress_percent or 0))
        filled = round(percent / 10)
        bar = "█" * filled + "░" * (10 - filled)
        embed.add_field(
            name="Download progress",
            value=f"`[{bar}]` **{percent}%**\nRefresh to see the latest download update.",
            inline=False,
        )
    milestones = [
        ("Source", project.status != "SOURCE_REVIEW_REQUIRED"),
        ("Downloaded", bool(project.source_storage_key)),
        ("Analyzed", state.analysis is not None and state.analysis.status == "COMPLETED"),
        ("Clips suggested", state.opportunity_count > 0),
        ("Rendered", state.total_clips > 0),
        ("Content ready", state.queued > 0),
    ]
    embed.add_field(
        name="Production pipeline",
        value="  ".join(f"{'✓' if complete else '○'} {label}" for label, complete in milestones),
        inline=False,
    )
    if project.last_error:
        embed.add_field(
            name="Needs attention",
            value="This video could not continue automatically. Open More Details for the safe error reference.",
            inline=False,
        )
    return embed


class GuidedProjectView(discord.ui.View):
    def __init__(
        self, project_id: uuid.UUID, repository: ProductionRepository, settings: Settings
    ) -> None:
        super().__init__(timeout=None)
        self.project_id, self.repository, self.settings = project_id, repository, settings

    async def _authorized(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member) or not is_authorized(
            interaction.user, self.settings
        ):
            await interaction.response.send_message(
                unauthorized_message(), view=OperatorAccessHelpView(self.settings), ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Continue Working", style=discord.ButtonStyle.success, custom_id="viralforge:guided:continue")
    async def continue_working(
        self, interaction: discord.Interaction, _: discord.ui.Button["GuidedProjectView"]
    ) -> None:
        if not await self._authorized(interaction):
            return
        state = await asyncio.to_thread(self.repository.dashboard, self.project_id)
        if state.project.status == "SOURCE_REVIEW_REQUIRED":
            try:
                await asyncio.to_thread(self.repository.accept_source, self.project_id)
            except ProductionError as error:
                await interaction.response.send_message(user_error(error), ephemeral=True)
                return
            await interaction.response.edit_message(
                content="Video accepted. ViralForge is downloading, inspecting, and preparing suggestions now.",
                embed=guided_project_embed(await asyncio.to_thread(self.repository.dashboard, self.project_id)),
                view=self,
            )
            return
        if state.pending_opportunity_count:
            pending = await asyncio.to_thread(
                self.repository.first_pending_opportunity_for_project, self.project_id
            )
            if pending is not None:
                await interaction.response.send_message(
                    embed=opportunity_embed(pending),
                    view=OpportunityReviewView(pending, self.repository, self.settings),
                    ephemeral=True,
                )
                return
        if state.approved_opportunity_count and not state.total_clips:
            await interaction.response.send_message(
                "Your approved clip is rendering automatically. Refresh this project for progress.",
                ephemeral=True,
            )
            return
        if state.opportunity_count and not state.total_clips:
            await interaction.response.send_message(
                "All suggested clips were declined. No clips will be rendered; choose another video when ready.",
                ephemeral=True,
            )
            return
        if state.total_clips and state.approved < state.total_clips:
            pending_clip = await asyncio.to_thread(self.repository.first_pending_clip)
            if pending_clip is not None:
                await interaction.response.send_message(
                    embed=clip_embed(pending_clip.clip, pending_clip.total),
                    view=ClipReviewView(pending_clip, self.repository, self.settings),
                    ephemeral=True,
                )
                return
        if state.queued:
            items = await asyncio.to_thread(self.repository.queue)
            await interaction.response.send_message(
                embed=ready_to_post_embed(items, self.settings), ephemeral=True
            )
            return
        await interaction.response.send_message(
            "ViralForge is continuing safely in the background. Refresh this page for the next decision.",
            ephemeral=True,
        )

    @discord.ui.button(label="Choose Another Video", style=discord.ButtonStyle.secondary, custom_id="viralforge:guided:alternatives")
    async def alternatives(
        self, interaction: discord.Interaction, _: discord.ui.Button["GuidedProjectView"]
    ) -> None:
        if not await self._authorized(interaction):
            return
        state = await asyncio.to_thread(self.repository.dashboard, self.project_id)
        candidates = await asyncio.to_thread(self.repository.sources, self.project_id)
        if not candidates:
            await interaction.response.send_message("No alternative videos are available for this item.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=candidate_embed(candidates),
            view=CandidateReviewView(
                self.project_id, state.project.source_decision_version, candidates, self.repository, self.settings
            ),
            ephemeral=True,
        )

    @discord.ui.button(label="More Details", style=discord.ButtonStyle.secondary, custom_id="viralforge:guided:details")
    async def details(
        self, interaction: discord.Interaction, _: discord.ui.Button["GuidedProjectView"]
    ) -> None:
        if not await self._authorized(interaction):
            return
        state = await asyncio.to_thread(self.repository.dashboard, self.project_id)
        await interaction.response.send_message(
            "Advanced details are shown below. They do not change the workflow.",
            embed=dashboard_embed(state),
            view=ProjectDashboardView(self.project_id, self.repository, self.settings, state),
            ephemeral=True,
        )

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, custom_id="viralforge:guided:refresh")
    async def refresh(
        self, interaction: discord.Interaction, _: discord.ui.Button["GuidedProjectView"]
    ) -> None:
        if not await self._authorized(interaction):
            return
        state = await asyncio.to_thread(self.repository.dashboard, self.project_id)
        await interaction.response.edit_message(embed=guided_project_embed(state), view=self)

    @discord.ui.button(label="Home", style=discord.ButtonStyle.secondary, custom_id="viralforge:guided:home")
    async def home(
        self, interaction: discord.Interaction, _: discord.ui.Button["GuidedProjectView"]
    ) -> None:
        if not await self._authorized(interaction):
            return
        state = await asyncio.to_thread(self.repository.control_center)
        await interaction.response.edit_message(
            embed=control_center_embed(state), view=OperatorHomeView(self.repository, self.settings)
        )


class AddVideoModal(discord.ui.Modal, title="Add a video"):
    url: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
        label="YouTube video URL", placeholder="https://www.youtube.com/watch?v=...", max_length=2048
    )

    def __init__(self, repository: ProductionRepository, settings: Settings) -> None:
        super().__init__()
        self.repository, self.settings = repository, settings

    async def on_submit(self, interaction: discord.Interaction) -> None:
        url = str(self.url).strip()
        if not url.startswith(("https://", "http://")):
            await interaction.response.send_message(
                "Add a complete public video URL beginning with https://, then try again.",
                view=RetryManualVideoView(self.repository, self.settings),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=manual_video_confirmation_embed(url),
            view=ManualVideoConfirmationView(url, self.repository, self.settings),
            ephemeral=True,
        )


def manual_video_confirmation_embed(url: str) -> discord.Embed:
    embed = discord.Embed(title="Confirm video", description="One short check before ViralForge begins processing.")
    embed.add_field(name="Video", value=url[:1024], inline=False)
    embed.add_field(
        name="Rights reminder",
        value="Use only approved, authorized, or public sources you are permitted to process.",
        inline=False,
    )
    embed.add_field(
        name="What happens next",
        value="ViralForge will resolve the source, then wait for your source approval before downloading.",
        inline=False,
    )
    return embed


class RetryManualVideoView(discord.ui.View):
    def __init__(self, repository: ProductionRepository, settings: Settings) -> None:
        super().__init__(timeout=300)
        self.repository, self.settings = repository, settings

    @discord.ui.button(label="Try Again", style=discord.ButtonStyle.primary)
    async def retry(self, interaction: discord.Interaction, _: discord.ui.Button["RetryManualVideoView"]) -> None:
        await interaction.response.send_modal(AddVideoModal(self.repository, self.settings))


class ManualVideoConfirmationView(discord.ui.View):
    def __init__(self, url: str, repository: ProductionRepository, settings: Settings) -> None:
        super().__init__(timeout=600)
        self.url, self.repository, self.settings = url, repository, settings

    @discord.ui.button(label="Create Project", style=discord.ButtonStyle.success)
    async def create(
        self, interaction: discord.Interaction, _: discord.ui.Button["ManualVideoConfirmationView"]
    ) -> None:
        # Resolving a public source can take longer than Discord's three-second
        # component acknowledgement window. Acknowledge first so the operator
        # never sees a false timeout while the idempotent project creation runs.
        await interaction.response.defer()
        try:
            project = await asyncio.to_thread(self.repository.create_project, self.url)
            state = await asyncio.to_thread(self.repository.dashboard, project.id)
        except ProductionError as error:
            await interaction.edit_original_response(
                content=user_error(error), view=RetryManualVideoView(self.repository, self.settings)
            )
            return
        await interaction.edit_original_response(
            content="Video added. Review it once, then ViralForge will prepare it automatically.",
            embed=guided_project_embed(state),
            view=GuidedProjectView(project.id, self.repository, self.settings),
        )

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button["ManualVideoConfirmationView"]) -> None:
        await interaction.response.send_modal(AddVideoModal(self.repository, self.settings))


def discovery_setup_embed(brand_name: str, selected: str = "YOUTUBE_CHANNEL") -> discord.Embed:
    embed = discord.Embed(
        title="Discovery setup",
        description=(
            f"**{brand_name}** has no approved discovery sources yet. "
            "Choose where ViralForge should look for public videos."
        ),
    )
    embed.add_field(name="1. Choose a source", value="YouTube Channel is ready to connect today.", inline=False)
    embed.add_field(
        name="2. Add the public reference",
        value="Paste an official YouTube channel URL, @handle, or channel ID.",
        inline=False,
    )
    embed.add_field(
        name="3. Confirm and scan",
        value=f"Selected: {selected.replace('_', ' ').title()}",
        inline=False,
    )
    embed.set_footer(text="Setup takes about one minute. You can change sources later.")
    return embed


class DiscoveryTypeSelect(discord.ui.Select["DiscoverySetupView"]):
    def __init__(self) -> None:
        super().__init__(
            placeholder="Where should ViralForge search?",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="YouTube Channel", value="YOUTUBE_CHANNEL", description="Available now"),
                discord.SelectOption(label="YouTube Playlist", value="YOUTUBE_PLAYLIST", description="Coming soon"),
                discord.SelectOption(label="RSS Feed", value="RSS", description="Coming soon in Discord"),
                discord.SelectOption(label="Website", value="WEBPAGE", description="Coming soon"),
                discord.SelectOption(label="Manual Import", value="MANUAL", description="Add one video now"),
                discord.SelectOption(label="Import Template", value="TEMPLATE", description="Coming soon"),
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        assert isinstance(view, DiscoverySetupView)
        view.source_kind = self.values[0]
        await interaction.response.edit_message(
            embed=discovery_setup_embed(view.brand_name, view.source_kind), view=view
        )


class DiscoverySetupView(discord.ui.View):
    def __init__(self, repository: DiscoveryRepository, settings: Settings, brand_name: str) -> None:
        super().__init__(timeout=600)
        self.repository, self.settings, self.brand_name = repository, settings, brand_name
        self.source_kind = "YOUTUBE_CHANNEL"
        self.add_item(DiscoveryTypeSelect())

    @discord.ui.button(label="Continue", style=discord.ButtonStyle.success)
    async def continue_setup(
        self, interaction: discord.Interaction, _: discord.ui.Button["DiscoverySetupView"]
    ) -> None:
        if self.source_kind == "YOUTUBE_CHANNEL":
            await interaction.response.send_modal(YouTubeChannelModal(self.repository, self.settings))
            return
        if self.source_kind == "MANUAL":
            await interaction.response.send_modal(AddVideoModal(self.repository, self.settings))
            return
        await interaction.response.send_message(
            f"**{self.source_kind.replace('_', ' ').title()} is coming soon.**\n"
            "You can add a YouTube Channel now, or add one video manually.",
            view=ComingSoonSetupView(self.repository, self.settings, self.brand_name),
            ephemeral=True,
        )

    @discord.ui.button(label="Help", style=discord.ButtonStyle.secondary)
    async def help(
        self, interaction: discord.Interaction, _: discord.ui.Button["DiscoverySetupView"]
    ) -> None:
        await interaction.response.send_message(
            "Discovery checks only public sources you explicitly configure. ViralForge never uses browser automation, cookies, or private accounts. A YouTube channel takes about one minute to add.",
            ephemeral=True,
        )

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(
        self, interaction: discord.Interaction, _: discord.ui.Button["DiscoverySetupView"]
    ) -> None:
        state = await asyncio.to_thread(self.repository.control_center)
        await interaction.response.edit_message(
            embed=control_center_embed(state), view=OperatorHomeView(self.repository, self.settings)
        )


class ComingSoonSetupView(discord.ui.View):
    def __init__(self, repository: DiscoveryRepository, settings: Settings, brand_name: str) -> None:
        super().__init__(timeout=300)
        self.repository, self.settings, self.brand_name = repository, settings, brand_name

    @discord.ui.button(label="Set Up YouTube", style=discord.ButtonStyle.primary)
    async def youtube(
        self, interaction: discord.Interaction, _: discord.ui.Button["ComingSoonSetupView"]
    ) -> None:
        await interaction.response.edit_message(
            embed=discovery_setup_embed(self.brand_name),
            view=DiscoverySetupView(self.repository, self.settings, self.brand_name),
        )

    @discord.ui.button(label="Add Video Instead", style=discord.ButtonStyle.secondary)
    async def manual(
        self, interaction: discord.Interaction, _: discord.ui.Button["ComingSoonSetupView"]
    ) -> None:
        await interaction.response.send_modal(AddVideoModal(self.repository, self.settings))


class YouTubeChannelModal(discord.ui.Modal, title="Add a YouTube channel"):
    reference: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
        label="Channel URL, @handle, or channel ID",
        placeholder="https://youtube.com/@PhoenixPolice",
        max_length=2048,
    )

    def __init__(self, repository: DiscoveryRepository, settings: Settings) -> None:
        super().__init__()
        self.repository, self.settings = repository, settings

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # Official YouTube API validation is a network request, so defer before
        # waiting on it rather than allowing Discord to time out the modal.
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            channel = await asyncio.to_thread(self.repository.preview_youtube_channel, str(self.reference))
        except ProductionError as error:
            await interaction.edit_original_response(
                content=user_error(error),
                view=RetryDiscoverySetupView(self.repository, self.settings),
            )
            return
        await interaction.edit_original_response(
            embed=youtube_channel_confirmation_embed(channel),
            view=DiscoverySourceConfirmationView(channel, self.repository, self.settings),
        )


def youtube_channel_confirmation_embed(channel: YouTubeChannel) -> discord.Embed:
    embed = discord.Embed(title="Confirm discovery source", description="We found this public YouTube channel.")
    embed.add_field(name="Channel", value=channel.title, inline=True)
    embed.add_field(name="Videos", value=str(channel.video_count) if channel.video_count is not None else "Unavailable", inline=True)
    embed.add_field(name="Latest upload", value=channel.latest_upload_title or "No recent upload found", inline=False)
    embed.add_field(name="Status", value="Ready to enable", inline=True)
    if channel.thumbnail_url:
        embed.set_thumbnail(url=channel.thumbnail_url)
    return embed


class RetryDiscoverySetupView(discord.ui.View):
    def __init__(self, repository: DiscoveryRepository, settings: Settings) -> None:
        super().__init__(timeout=300)
        self.repository, self.settings = repository, settings

    @discord.ui.button(label="Try Again", style=discord.ButtonStyle.primary)
    async def retry(self, interaction: discord.Interaction, _: discord.ui.Button["RetryDiscoverySetupView"]) -> None:
        await interaction.response.send_modal(YouTubeChannelModal(self.repository, self.settings))

    @discord.ui.button(label="Add Video Instead", style=discord.ButtonStyle.secondary)
    async def manual(self, interaction: discord.Interaction, _: discord.ui.Button["RetryDiscoverySetupView"]) -> None:
        await interaction.response.send_modal(AddVideoModal(self.repository, self.settings))


class DiscoverySourceConfirmationView(discord.ui.View):
    def __init__(self, channel: YouTubeChannel, repository: DiscoveryRepository, settings: Settings) -> None:
        super().__init__(timeout=600)
        self.channel, self.repository, self.settings = channel, repository, settings

    @discord.ui.button(label="Enable Source", style=discord.ButtonStyle.success)
    async def enable(
        self, interaction: discord.Interaction, _: discord.ui.Button["DiscoverySourceConfirmationView"]
    ) -> None:
        try:
            source = await asyncio.to_thread(self.repository.enable_youtube_channel, self.channel)
        except ProductionError:
            await interaction.response.send_message(
                "We could not enable that source yet. Try the validation again or add a video manually.",
                view=RetryDiscoverySetupView(self.repository, self.settings),
                ephemeral=True,
            )
            return
        await interaction.response.edit_message(
            content="Discovery source enabled. You can scan it now or return home.",
            embed=youtube_channel_confirmation_embed(self.channel),
            view=DiscoveryRunNowView(source, self.repository, self.settings),
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, _: discord.ui.Button["DiscoverySourceConfirmationView"]
    ) -> None:
        state = await asyncio.to_thread(self.repository.control_center)
        await interaction.response.edit_message(
            embed=control_center_embed(state), view=OperatorHomeView(self.repository, self.settings)
        )


class DiscoveryRunNowView(discord.ui.View):
    def __init__(self, source: DiscoverySource, repository: DiscoveryRepository, settings: Settings) -> None:
        super().__init__(timeout=600)
        self.source, self.repository, self.settings = source, repository, settings

    @discord.ui.button(label="Run Discovery Now", style=discord.ButtonStyle.primary)
    async def run_now(
        self, interaction: discord.Interaction, _: discord.ui.Button["DiscoveryRunNowView"]
    ) -> None:
        await interaction.response.defer(thinking=True)
        try:
            run = await asyncio.to_thread(self.repository.run, self.source.id)
        except ProductionError:
            await interaction.edit_original_response(
                content="The scan could not start yet. Check the source and try again; nothing was changed.",
                view=RetryDiscoverySetupView(self.repository, self.settings),
            )
            return
        if getattr(run, "status", "FAILED") != "SUCCEEDED":
            await interaction.edit_original_response(
                content="The scan could not finish yet. Check the source and try again; nothing was changed.",
                view=RetryDiscoverySetupView(self.repository, self.settings),
            )
            return
        await interaction.edit_original_response(
            content=(
                f"Found {run.new_count} new video{'s' if run.new_count != 1 else ''}. "
                f"{run.duplicate_count} duplicate{'s were' if run.duplicate_count != 1 else ' was'} safely skipped."
            ),
            view=ReviewFoundVideosView(self.repository, self.settings),
        )

    @discord.ui.button(label="Back Home", style=discord.ButtonStyle.secondary)
    async def home(self, interaction: discord.Interaction, _: discord.ui.Button["DiscoveryRunNowView"]) -> None:
        state = await asyncio.to_thread(self.repository.control_center)
        await interaction.response.edit_message(
            embed=control_center_embed(state), view=OperatorHomeView(self.repository, self.settings)
        )


class ReviewFoundVideosView(discord.ui.View):
    def __init__(self, repository: DiscoveryRepository, settings: Settings) -> None:
        super().__init__(timeout=300)
        self.repository, self.settings = repository, settings

    @discord.ui.button(label="Review Videos", style=discord.ButtonStyle.success)
    async def review(self, interaction: discord.Interaction, _: discord.ui.Button["ReviewFoundVideosView"]) -> None:
        items = await asyncio.to_thread(self.repository.discovery_queue)
        if not items:
            await interaction.response.send_message(
                "The scan finished, but no videos matched this brand's review rules. Add a video manually or adjust the source later.",
                view=RetryDiscoverySetupView(self.repository, self.settings),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=discovery_embed(items[0]),
            view=DiscoveryReviewView(items[0], self.repository, self.settings),
            ephemeral=True,
        )

    @discord.ui.button(label="Return Home", style=discord.ButtonStyle.secondary)
    async def home(self, interaction: discord.Interaction, _: discord.ui.Button["ReviewFoundVideosView"]) -> None:
        state = await asyncio.to_thread(self.repository.control_center)
        await interaction.response.edit_message(
            embed=control_center_embed(state), view=OperatorHomeView(self.repository, self.settings)
        )


class ContentReadySetupView(discord.ui.View):
    """Guidance boundary for publishing setup; credentials never enter Discord."""
    def __init__(self, repository: ProductionRepository, settings: Settings) -> None:
        super().__init__(timeout=300)
        self.repository, self.settings = repository, settings

    @discord.ui.button(label="Set Up YouTube", style=discord.ButtonStyle.primary)
    async def youtube(
        self, interaction: discord.Interaction, _: discord.ui.Button["ContentReadySetupView"]
    ) -> None:
        await interaction.response.send_message(
            "**Connect a YouTube account**\n"
            "An administrator must finish the secure OAuth setup and store only the credential reference outside Discord. "
            "ViralForge will never ask for a token, password, or cookie here. TikTok, Instagram, Facebook, and X are coming soon.",
            ephemeral=True,
        )

    @discord.ui.button(label="Find Videos", style=discord.ButtonStyle.secondary)
    async def find(
        self, interaction: discord.Interaction, _: discord.ui.Button["ContentReadySetupView"]
    ) -> None:
        state = await asyncio.to_thread(self.repository.control_center)
        await interaction.response.send_message(
            embed=discovery_setup_embed(state.active_brand_name),
            view=DiscoverySetupView(
                DiscoveryRepository(self.settings), self.settings, state.active_brand_name
            ),
            ephemeral=True,
        )

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button["ContentReadySetupView"]) -> None:
        state = await asyncio.to_thread(self.repository.control_center)
        await interaction.response.edit_message(
            embed=control_center_embed(state), view=OperatorHomeView(self.repository, self.settings)
        )


class OperatorHomeView(discord.ui.View):
    """The compact default navigation. Legacy controls remain available in More."""
    def __init__(self, repository: ProductionRepository, settings: Settings) -> None:
        super().__init__(timeout=None)
        self.repository, self.settings = repository, settings

    async def _authorized(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member) or not is_authorized(interaction.user, self.settings):
            await interaction.response.send_message(
                unauthorized_message(), view=OperatorAccessHelpView(self.settings), ephemeral=True
            )
            return False
        return True

    async def _open_next(self, interaction: discord.Interaction) -> None:
        projects = await asyncio.to_thread(self.repository.projects, "SOURCE_REVIEW_REQUIRED")
        if projects:
            state = await asyncio.to_thread(self.repository.dashboard, projects[0].id)
            await interaction.response.send_message(embed=guided_project_embed(state), view=GuidedProjectView(state.project.id, self.repository, self.settings), ephemeral=True)
            return
        opportunity = await asyncio.to_thread(self.repository.first_pending_opportunity)
        if opportunity is not None:
            await interaction.response.send_message(embed=opportunity_embed(opportunity), view=OpportunityReviewView(opportunity, self.repository, self.settings), ephemeral=True)
            return
        clip = await asyncio.to_thread(self.repository.first_pending_clip)
        if clip is not None:
            await interaction.response.send_message(embed=clip_embed(clip.clip, clip.total), view=ClipReviewView(clip, self.repository, self.settings), ephemeral=True)
            return
        home_state = await asyncio.to_thread(self.repository.control_center)
        discovery_repository = DiscoveryRepository(self.settings)
        if not home_state.discovery_source_count:
            await interaction.response.send_message(
                embed=discovery_setup_embed(home_state.active_brand_name),
                view=DiscoverySetupView(
                    discovery_repository, self.settings, home_state.active_brand_name
                ),
                ephemeral=True,
            )
            return
        items = await asyncio.to_thread(discovery_repository.discovery_queue)
        if items:
            await interaction.response.send_message(
                embed=discovery_embed(items[0]),
                view=DiscoveryReviewView(items[0], discovery_repository, self.settings),
                ephemeral=True,
            )
            return
        queue = await asyncio.to_thread(self.repository.queue)
        if queue:
            await interaction.response.send_message(embed=ready_to_post_embed(queue, self.settings), ephemeral=True)
            return
        await interaction.response.send_message("Everything is caught up. Add a video or find a new one to continue.", ephemeral=True)

    @discord.ui.button(
        label="Continue Working",
        style=discord.ButtonStyle.success,
        custom_id="viralforge:operator:continue",
    )
    async def continue_working(
        self, interaction: discord.Interaction, _: discord.ui.Button["OperatorHomeView"]
    ) -> None:
        if await self._authorized(interaction):
            await self._open_next(interaction)

    @discord.ui.button(label="Find Videos", style=discord.ButtonStyle.secondary, custom_id="viralforge:operator:find")
    async def find_videos(self, interaction: discord.Interaction, _: discord.ui.Button["OperatorHomeView"]) -> None:
        if not await self._authorized(interaction):
            return
        state = await asyncio.to_thread(self.repository.control_center)
        if not state.discovery_source_count:
            await interaction.response.send_message(
                embed=discovery_setup_embed(state.active_brand_name),
                view=DiscoverySetupView(
                    DiscoveryRepository(self.settings), self.settings, state.active_brand_name
                ),
                ephemeral=True,
            )
            return
        items = await asyncio.to_thread(DiscoveryRepository(self.settings).discovery_queue)
        if not items:
            await interaction.response.send_message("ViralForge is checking your approved sources. No videos need review yet.", ephemeral=True)
            return
        await interaction.response.send_message(embed=discovery_embed(items[0]), view=DiscoveryReviewView(items[0], DiscoveryRepository(self.settings), self.settings), ephemeral=True)

    @discord.ui.button(label="Add Video", style=discord.ButtonStyle.primary, custom_id="viralforge:operator:add")
    async def add_video(self, interaction: discord.Interaction, _: discord.ui.Button["OperatorHomeView"]) -> None:
        if await self._authorized(interaction):
            await interaction.response.send_modal(AddVideoModal(self.repository, self.settings))

    @discord.ui.button(label="Review", style=discord.ButtonStyle.primary, custom_id="viralforge:operator:review")
    async def review(
        self, interaction: discord.Interaction, _: discord.ui.Button["OperatorHomeView"]
    ) -> None:
        if await self._authorized(interaction):
            await self._open_next(interaction)

    @discord.ui.button(label="Ready To Post", style=discord.ButtonStyle.secondary, custom_id="viralforge:operator:ready")
    async def ready_to_post(self, interaction: discord.Interaction, _: discord.ui.Button["OperatorHomeView"]) -> None:
        if not await self._authorized(interaction):
            return
        items = await asyncio.to_thread(self.repository.queue)
        await interaction.response.send_message(
            embed=ready_to_post_embed(items, self.settings),
            view=ContentReadySetupView(self.repository, self.settings),
            ephemeral=True,
        )

    @discord.ui.button(label="More", style=discord.ButtonStyle.secondary, custom_id="viralforge:operator:more")
    async def more(self, interaction: discord.Interaction, _: discord.ui.Button["OperatorHomeView"]) -> None:
        if not await self._authorized(interaction):
            return
        await interaction.response.send_message(
            "Advanced tools and diagnostics are available without changing your active workflow.",
            view=AdvancedOperatorView(self.repository, self.settings), ephemeral=True,
        )


class AdvancedOperatorView(discord.ui.View):
    def __init__(self, repository: ProductionRepository, settings: Settings) -> None:
        super().__init__(timeout=300)
        self.repository, self.settings = repository, settings

    @discord.ui.button(label="Projects", style=discord.ButtonStyle.secondary)
    async def projects(self, interaction: discord.Interaction, _: discord.ui.Button["AdvancedOperatorView"]) -> None:
        projects = await asyncio.to_thread(self.repository.projects)
        await interaction.response.send_message(embed=projects_embed(projects, "Projects", "No videos have been added yet."), view=ProjectListView(projects, self.repository, self.settings), ephemeral=True)

    @discord.ui.button(label="System Details", style=discord.ButtonStyle.secondary)
    async def status(self, interaction: discord.Interaction, _: discord.ui.Button["AdvancedOperatorView"]) -> None:
        status = operational_status(self.settings)
        await interaction.response.send_message("\n".join(f"{name}: {'ready' if value else 'not configured'}" for name, value in status.items()), ephemeral=True)

    @discord.ui.button(label="Choose Brand", style=discord.ButtonStyle.secondary)
    async def brands(self, interaction: discord.Interaction, _: discord.ui.Button["AdvancedOperatorView"]) -> None:
        brands = await asyncio.to_thread(self.repository.brands)
        await interaction.response.send_message("Choose the brand you are working on.", view=BrandSelectionView(brands, self.repository, self.settings), ephemeral=True)


class ViralForgeBot(discord.Client):
    def __init__(
        self, repository: ProductionRepository | None = None, settings: Settings | None = None
    ) -> None:
        intents = discord.Intents(guilds=True, members=True, message_content=True)
        super().__init__(intents=intents)
        self.settings, self.repository = (
            settings or get_settings(),
            repository or ProductionRepository(settings),
        )
        self.discovery_repository = DiscoveryRepository(self.settings)
        self._business_presence_task: asyncio.Task[None] | None = None
        self._automod_recent: dict[tuple[int, int, int], int] = {}
        self.tree = app_commands.CommandTree(self)
        self.tree.add_command(
            app_commands.Group(name="viralforge", description="ViralForge clipping controls")
        )
        self.tree.add_command(
            app_commands.Group(name="discovery", description="Public-video discovery controls")
        )

    async def review_channel(self) -> discord.TextChannel:
        if not self.settings.discord_review_channel_id:
            raise RuntimeError("DISCORD_REVIEW_CHANNEL_ID is required to submit a project")
        try:
            channel_id = int(self.settings.discord_review_channel_id)
        except ValueError as error:
            raise RuntimeError("DISCORD_REVIEW_CHANNEL_ID must be a Discord snowflake") from error
        channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError("DISCORD_REVIEW_CHANNEL_ID must identify a text channel")
        return channel

    async def setup_hook(self) -> None:
        self.add_view(ControlCenterView(self.repository, self.settings))
        self.add_view(OperatorHomeView(self.repository, self.settings))
        for project_id in await asyncio.to_thread(self.repository.active_dashboard_projects):
            try:
                state = await asyncio.to_thread(self.repository.dashboard, project_id)
            except ProductionError:
                continue
            self.add_view(ProjectDashboardView(project_id, self.repository, self.settings, state))
            self.add_view(GuidedProjectView(project_id, self.repository, self.settings))
        for clip_id in await asyncio.to_thread(self.repository.active_review_clips):
            try:
                clip_state = await asyncio.to_thread(self.repository.review_state, clip_id)
            except ProductionError:
                continue
            self.add_view(ClipReviewView(clip_state, self.repository, self.settings))
        for opportunity_id in await asyncio.to_thread(self.repository.active_opportunity_ids):
            try:
                opportunity_state = await asyncio.to_thread(
                    self.repository.opportunity_state, opportunity_id
                )
            except ProductionError:
                continue
            self.add_view(OpportunityReviewView(opportunity_state, self.repository, self.settings))
        register_business_commands(self)
        self._business_presence_task = asyncio.create_task(self._rotate_business_presence())
        group = self.tree.get_command("viralforge")
        assert isinstance(group, app_commands.Group)
        discovery_group = self.tree.get_command("discovery")
        assert isinstance(discovery_group, app_commands.Group)

        @discovery_group.command(name="status", description="Show discovery readiness")
        async def discovery_status(interaction: discord.Interaction) -> None:
            if not isinstance(interaction.user, discord.Member) or not is_authorized(
                interaction.user, self.settings
            ):
                await interaction.response.send_message(
                    unauthorized_message(), view=OperatorAccessHelpView(self.settings), ephemeral=True
                )
                return
            await interaction.response.send_message(
                f"discovery: {'enabled' if self.settings.discovery_enabled else 'disabled'}\nscheduler: {'enabled' if self.settings.discovery_scheduler_enabled else 'disabled'}",
                ephemeral=True,
            )

        @discovery_group.command(name="queue", description="Show discovery items awaiting review")
        async def discovery_queue(interaction: discord.Interaction) -> None:
            if not isinstance(interaction.user, discord.Member) or not is_authorized(
                interaction.user, self.settings
            ):
                await interaction.response.send_message(
                    unauthorized_message(), view=OperatorAccessHelpView(self.settings), ephemeral=True
                )
                return
            items = await asyncio.to_thread(self.discovery_repository.discovery_queue)
            if not items:
                await interaction.response.send_message(
                    "No discovery items require review.", ephemeral=True
                )
                return
            await interaction.response.send_message(
                embed=discovery_embed(items[0]),
                view=DiscoveryReviewView(items[0], self.discovery_repository, self.settings),
                ephemeral=True,
            )

        @discovery_group.command(
            name="approve", description="Approve a discovered item for source review"
        )
        async def discovery_approve(interaction: discord.Interaction, media_id: str) -> None:
            if not isinstance(interaction.user, discord.Member) or not is_authorized(
                interaction.user, self.settings
            ):
                await interaction.response.send_message(
                    unauthorized_message(), view=OperatorAccessHelpView(self.settings), ephemeral=True
                )
                return
            try:
                media = await asyncio.to_thread(
                    self.discovery_repository.media, uuid.UUID(media_id)
                )
                media = await asyncio.to_thread(
                    self.discovery_repository.approve, media.id, media.review_version
                )
            except (ValueError, ProductionError) as error:
                await interaction.response.send_message(
                    getattr(error, "code", "DISCOVERY_NOT_FOUND"), ephemeral=True
                )
                return
            await interaction.response.send_message(embed=discovery_embed(media), ephemeral=True)

        @discovery_group.command(name="reject", description="Reject a discovered item")
        async def discovery_reject(interaction: discord.Interaction, media_id: str) -> None:
            if not isinstance(interaction.user, discord.Member) or not is_authorized(
                interaction.user, self.settings
            ):
                await interaction.response.send_message(
                    unauthorized_message(), view=OperatorAccessHelpView(self.settings), ephemeral=True
                )
                return
            try:
                media = await asyncio.to_thread(
                    self.discovery_repository.media, uuid.UUID(media_id)
                )
                media = await asyncio.to_thread(
                    self.discovery_repository.reject, media.id, media.review_version
                )
            except (ValueError, ProductionError) as error:
                await interaction.response.send_message(
                    getattr(error, "code", "DISCOVERY_NOT_FOUND"), ephemeral=True
                )
                return
            await interaction.response.send_message(embed=discovery_embed(media), ephemeral=True)

        @group.command(name="submit", description="Create a project from an authorized YouTube URL")
        async def submit(interaction: discord.Interaction, url: str) -> None:
            if not isinstance(interaction.user, discord.Member) or not is_authorized(
                interaction.user, self.settings
            ):
                await interaction.response.send_message(
                    unauthorized_message(), view=OperatorAccessHelpView(self.settings), ephemeral=True
                )
                return
            try:
                project = await asyncio.to_thread(self.repository.create_project, url)
            except ProductionError as error:
                await interaction.response.send_message(user_error(error), ephemeral=True)
                return
            state = await asyncio.to_thread(self.repository.dashboard, project.id)
            try:
                channel = await self.review_channel()
                message = await channel.send(
                    embed=guided_project_embed(state),
                    view=GuidedProjectView(project.id, self.repository, self.settings),
                )
            except discord.DiscordException:
                await interaction.response.send_message(
                    "Unable to post the project dashboard to the review channel.", ephemeral=True
                )
                return
            await asyncio.to_thread(
                self.repository.set_dashboard_message,
                project.id,
                channel.guild.id,
                channel.id,
                message.id,
            )
            await interaction.response.send_message(
                f"Project dashboard created in {channel.mention}.", ephemeral=True
            )

        @group.command(name="project", description="Show a production project dashboard")
        async def project(interaction: discord.Interaction, project_id: str) -> None:
            if not isinstance(interaction.user, discord.Member) or not is_authorized(
                interaction.user, self.settings
            ):
                await interaction.response.send_message(
                    unauthorized_message(), view=OperatorAccessHelpView(self.settings), ephemeral=True
                )
                return
            try:
                state = await asyncio.to_thread(self.repository.dashboard, uuid.UUID(project_id))
            except (ValueError, ProductionError):
                await interaction.response.send_message("Project not found.", ephemeral=True)
                return
            await interaction.response.send_message(
                embed=guided_project_embed(state),
                view=GuidedProjectView(state.project.id, self.repository, self.settings),
                ephemeral=True,
            )

        @self.tree.command(name="home", description="Open your guided ViralForge workspace")
        async def operator_home(interaction: discord.Interaction) -> None:
            if not isinstance(interaction.user, discord.Member) or not is_authorized(
                interaction.user, self.settings
            ):
                await interaction.response.send_message(
                    unauthorized_message(), view=OperatorAccessHelpView(self.settings), ephemeral=True
                )
                return
            state = await asyncio.to_thread(self.repository.control_center)
            await interaction.response.send_message(
                embed=control_center_embed(state), view=OperatorHomeView(self.repository, self.settings)
            )

        @group.command(name="home", description="Open your guided ViralForge workspace")
        async def home(interaction: discord.Interaction) -> None:
            if not isinstance(interaction.user, discord.Member) or not is_authorized(
                interaction.user, self.settings
            ):
                await interaction.response.send_message(
                    unauthorized_message(), view=OperatorAccessHelpView(self.settings), ephemeral=True
                )
                return
            state = await asyncio.to_thread(self.repository.control_center)
            await interaction.response.send_message(
                embed=control_center_embed(state),
                view=OperatorHomeView(self.repository, self.settings),
            )

        @group.command(name="review", description="Open the next item needing your creative decision")
        async def review(interaction: discord.Interaction) -> None:
            if not isinstance(interaction.user, discord.Member) or not is_authorized(
                interaction.user, self.settings
            ):
                await interaction.response.send_message(
                    unauthorized_message(), view=OperatorAccessHelpView(self.settings), ephemeral=True
                )
                return
            source_review = await asyncio.to_thread(
                self.repository.projects, "SOURCE_REVIEW_REQUIRED"
            )
            if source_review:
                state = await asyncio.to_thread(self.repository.dashboard, source_review[0].id)
                await interaction.response.send_message(
                    embed=guided_project_embed(state),
                    view=GuidedProjectView(state.project.id, self.repository, self.settings),
                    ephemeral=True,
                )
                return
            opportunity = await asyncio.to_thread(self.repository.first_pending_opportunity)
            if opportunity is not None:
                await interaction.response.send_message(
                    embed=opportunity_embed(opportunity),
                    view=OpportunityReviewView(opportunity, self.repository, self.settings),
                    ephemeral=True,
                )
                return
            clip = await asyncio.to_thread(self.repository.first_pending_clip)
            if clip is not None:
                await interaction.response.send_message(
                    embed=clip_embed(clip.clip, clip.total),
                    view=ClipReviewView(clip, self.repository, self.settings),
                    ephemeral=True,
                )
                return
            discovery = await asyncio.to_thread(self.discovery_repository.discovery_queue)
            if discovery:
                await interaction.response.send_message(
                    embed=discovery_embed(discovery[0]),
                    view=DiscoveryReviewView(
                        discovery[0], self.discovery_repository, self.settings
                    ),
                    ephemeral=True,
                )
                return
            await interaction.response.send_message("Nothing needs a creative decision right now.", ephemeral=True)

        @group.command(name="projects", description="List recent production projects")
        async def projects(interaction: discord.Interaction) -> None:
            if not isinstance(interaction.user, discord.Member) or not is_authorized(
                interaction.user, self.settings
            ):
                await interaction.response.send_message(
                    unauthorized_message(), view=OperatorAccessHelpView(self.settings), ephemeral=True
                )
                return
            projects = await asyncio.to_thread(self.repository.projects)
            await interaction.response.send_message(
                embed=projects_embed(
                    projects, "Production projects", "No production projects have been submitted."
                ),
                view=ProjectListView(projects, self.repository, self.settings),
                ephemeral=True,
            )

        @group.command(name="brands", description="Select the active workspace brand")
        async def brands(interaction: discord.Interaction) -> None:
            if not isinstance(interaction.user, discord.Member) or not is_authorized(
                interaction.user, self.settings
            ):
                await interaction.response.send_message(
                    unauthorized_message(), view=OperatorAccessHelpView(self.settings), ephemeral=True
                )
                return
            brands = await asyncio.to_thread(self.repository.brands)
            active = await asyncio.to_thread(self.repository.default_brand)
            body = (
                "\n".join(
                    f"â€¢ {brand.name}{' (active)' if brand.id == active.id else ''}"
                    for brand in brands
                )
                or "No brands are assigned."
            )
            await interaction.response.send_message(
                f"**Brands**\n{body[:1800]}",
                view=BrandSelectionView(brands, self.repository, self.settings),
                ephemeral=True,
            )

        @group.command(name="queue", description="Show content ready for the next publishing decision")
        async def queue(interaction: discord.Interaction) -> None:
            if not isinstance(interaction.user, discord.Member) or not is_authorized(
                interaction.user, self.settings
            ):
                await interaction.response.send_message(
                    unauthorized_message(), view=OperatorAccessHelpView(self.settings), ephemeral=True
                )
                return
            items = await asyncio.to_thread(self.repository.queue)
            await interaction.response.send_message(
                embed=ready_to_post_embed(items, self.settings), ephemeral=True
            )
            return
            body = (
                "\n".join(
                    f"• {project.source_title or 'Project'} — clip {clip.clip_number}: {item.caption or 'No caption'}"
                    for item, clip, project in items
                )
                or "No clips are ready to post."
            )
            await interaction.response.send_message(body[:2_000], ephemeral=True)

        @group.command(
            name="publish", description="Open the required explicit publishing confirmation"
        )
        async def publish(interaction: discord.Interaction, request_id: str) -> None:
            if not isinstance(interaction.user, discord.Member) or not is_authorized(
                interaction.user, self.settings
            ):
                await interaction.response.send_message(
                    unauthorized_message(), view=OperatorAccessHelpView(self.settings), ephemeral=True
                )
                return
            try:
                request = await asyncio.to_thread(
                    self.repository.publish_request, uuid.UUID(request_id)
                )
            except ValueError:
                request = None
            if request is None:
                await interaction.response.send_message(
                    "Publishing request not found.", ephemeral=True
                )
                return
            if request.status != "AWAITING_CONFIRMATION":
                await interaction.response.send_message(
                    "This publishing request is no longer awaiting confirmation.", ephemeral=True
                )
                return
            await interaction.response.send_message(
                embed=publish_confirmation_embed(request),
                view=PublishConfirmationView(request.id, self.repository, self.settings),
                ephemeral=True,
            )

        @group.command(name="status", description="Show safe clipping-operation readiness")
        async def status(interaction: discord.Interaction) -> None:
            state = operational_status(self.settings)
            body = "\n".join(
                f"{name}: {'ready' if ready else 'not configured'}" for name, ready in state.items()
            )
            await interaction.response.send_message(body, ephemeral=True)

        @group.command(
            name="analytics",
            description="Show read-only performance and recommendations for the active brand",
        )
        async def analytics(interaction: discord.Interaction) -> None:
            if not isinstance(interaction.user, discord.Member) or not is_authorized(
                interaction.user, self.settings
            ):
                await interaction.response.send_message(
                    unauthorized_message(), view=OperatorAccessHelpView(self.settings), ephemeral=True
                )
                return
            brand = await asyncio.to_thread(self.repository.default_brand)
            session = next(get_session())
            try:
                summary = analytics_dashboard(session, brand.id)
            finally:
                session.close()
            recommendations = (
                "\n".join(f"• {item}" for item in summary["recommendations"][:3])
                or "No recommendation yet; collect snapshots first."
            )
            await interaction.response.send_message(
                f"**Analytics — {brand.name}**\nPublished posts: {summary['published_posts']}\nRecorded views: {summary['views']}\nAverage retention: {summary['average_retention_percentage'] or 'unavailable'}\n\n**Recommendations (observe only)**\n{recommendations}",
                ephemeral=True,
            )

        if self.settings.discord_guild_id:
            try:
                guild = discord.Object(id=int(self.settings.discord_guild_id))
            except ValueError as error:
                raise RuntimeError("DISCORD_GUILD_ID must be a Discord snowflake") from error
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_message(self, message: discord.Message) -> None:
        """Apply deterministic, redacted safety checks without retaining message bodies."""
        if message.guild is None or message.author.bot or not isinstance(message.author, discord.Member):
            return
        if is_business_staff(message.author):
            return
        config = load_config()
        channel_key = str(getattr(message.channel, "name", "")).replace("-", "_")
        content_hash = hash(message.content)
        cache_key = (message.guild.id, message.author.id, content_hash)
        repeated = self._automod_recent.get(cache_key, 0) >= int(
            config["automod"]["rules"]["repeated_message"]["repeats"] - 1
        )
        self._automod_recent[cache_key] = self._automod_recent.get(cache_key, 0) + 1
        finding = scan_message(message.content, mention_count=len(message.mentions), repeated=repeated)
        if finding is None:
            return
        if finding.rule_key == "discord_invite" and channel_key in set(
            config["automod"]["rules"]["discord_invite"].get("allowed_channel_keys", [])
        ):
            return
        deleted = False
        if finding.action in {"DELETE_AND_REVIEW", "WARN_AND_REVIEW"}:
            try:
                await message.delete()
                deleted = True
            except discord.Forbidden:
                pass
            except discord.NotFound:
                return
        session = next(get_session())
        try:
            case = OperationsRepository().create_case(session, message.guild.id, message.author.id, finding)
            session.commit()
            case_number = case.case_number
        except Exception:
            session.rollback()
            return
        finally:
            session.close()
        with contextlib.suppress(discord.DiscordException):
            await message.author.send(
                "Your message was removed or reviewed by ViralForge safety controls. "
                f"Case #{case_number}. Do not share credentials, tokens, or private URLs in Discord."
            )
        session = next(get_session())
        try:
            channel_id = BusinessRepository().resource_id(session, message.guild.id, "channel", "operator_alerts")
        finally:
            session.close()
        if channel_id:
            channel = message.guild.get_channel(channel_id)
            if isinstance(channel, discord.TextChannel):
                await channel.send(
                    f"Automod case #{case_number}: {finding.rule_key}; action {'deleted' if deleted else 'review'}; "
                    f"member {message.author.mention}. Evidence is redacted.",
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )

    async def _rotate_business_presence(self) -> None:
        position = 0
        while not self.is_closed():
            try:
                position = await apply_business_presence(self, position)
            except Exception:
                # Presence must never prevent the control plane from starting.
                pass
            await asyncio.sleep(business_presence_interval())

    async def close(self) -> None:
        if self._business_presence_task is not None:
            self._business_presence_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._business_presence_task
        await super().close()


def run_bot() -> None:
    settings = get_settings()
    if not settings.discord_bot_token:
        raise RuntimeError("DISCORD_BOT_TOKEN is required to start the ViralForge Discord bot")
    ViralForgeBot(settings=settings).run(settings.discord_bot_token)


if __name__ == "__main__":
    run_bot()
