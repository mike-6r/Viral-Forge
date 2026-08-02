import uuid
from pathlib import Path

from celery import Celery

from app.common.config import get_settings
from app.common.db import get_session

settings = get_settings()
celery_app = Celery(
    "viralforge", broker=settings.celery_broker_url, backend=settings.celery_result_backend
)
celery_app.conf.update(
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    worker_concurrency=settings.analysis_max_concurrency,
)
celery_app.conf.beat_schedule = {
    "scheduler-heartbeat": {"task": "viralforge.scheduler_heartbeat", "schedule": settings.scheduler_heartbeat_interval_seconds},
    "cleanup-expired-media": {"task": "viralforge.cleanup_expired_media", "schedule": settings.cleanup_interval_seconds},
    "refresh-published-analytics": {"task": "viralforge.refresh_published_analytics", "schedule": 3600},
    "evaluate-producer-predictions": {"task": "viralforge.evaluate_producer_predictions", "schedule": 3600},
    "execute-due-publish-requests": {"task": "viralforge.execute_due_publish_requests", "schedule": 60},
    "refresh-due-tiktok-publish-requests": {"task": "viralforge.refresh_due_tiktok_publish_requests", "schedule": 60},
    "refresh-tiktok-credentials": {"task": "viralforge.refresh_tiktok_credentials", "schedule": 900},
    "poll-due-discovery-sources": {"task": "viralforge.discovery_poll_due_sources", "schedule": 300},
}


@celery_app.task(name="viralforge.heartbeat")
def application_heartbeat() -> dict[str, str]:
    return {"status": "ok", "service": "viralforge-worker"}


@celery_app.task(name="viralforge.scheduler_heartbeat")
def scheduler_heartbeat() -> dict[str, str]:
    """A bounded Beat heartbeat; scheduler liveness remains visible in worker logs."""
    return {"status": "ok", "service": "viralforge-scheduler"}


@celery_app.task(name="viralforge.stale_job_detection_preview")
def stale_job_detection_preview() -> dict[str, str]:
    return {
        "status": "preview",
        "message": "Recovery will mark expired RUNNING jobs STALE and enqueue idempotent retries.",
    }


@celery_app.task(name="viralforge.audit_cleanup_preview")
def audit_cleanup_preview() -> dict[str, str]:
    return {
        "status": "preview",
        "message": "No records deleted; retention policy is not configured in Milestone 1.",
    }


@celery_app.task(name="viralforge.discovery_poll_due_sources")
def discovery_poll_due_sources() -> dict[str, int | str]:
    """One bounded scheduler tick; no infinite loop is run in the API process."""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from app.accounts.models import Role, RoleName, User, UserRole
    from app.discovery.models import DiscoverySource
    from app.discovery.service import run_source

    if not settings.discovery_enabled or not settings.discovery_scheduler_enabled:
        return {"status": "disabled", "processed": 0}
    session = next(get_session())
    try:
        actor = session.scalar(
            select(User.id)
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(User.is_active, Role.name.in_([RoleName.OWNER, RoleName.ADMIN]))
            .order_by(User.created_at)
        )
        if actor is None:
            return {"status": "no_actor", "processed": 0}
        now = datetime.now(UTC)
        sources = list(
            session.scalars(
                select(DiscoverySource)
                .where(
                    DiscoverySource.enabled,
                    (DiscoverySource.next_poll_at.is_(None))
                    | (DiscoverySource.next_poll_at <= now),
                )
                .order_by(DiscoverySource.next_poll_at)
                .limit(settings.discovery_provider_concurrency)
            )
        )
        for source in sources:
            run_source(session, actor, source)
        return {"status": "ok", "processed": len(sources)}
    finally:
        session.close()


@celery_app.task(name="viralforge.run_video_analysis")
def run_video_analysis(
    project_id: str, rerun: bool = False, analysis_version: str | None = None
) -> dict[str, str]:
    """Run one persisted analysis job; duplicate deliveries are safe and side-effect free."""
    from sqlalchemy import select

    from app.accounts.models import Role, RoleName, User, UserRole
    from app.analysis.models import VideoAnalysis
    from app.analysis.service import execute_analysis, request_analysis
    from app.ingestion.storage import LocalFilesystemStorage
    from app.production.models import ProductionProject

    session = next(get_session())
    try:
        actor = session.scalar(
            select(User.id)
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(User.is_active, Role.name.in_([RoleName.OWNER, RoleName.ADMIN]))
            .order_by(User.created_at)
        )
        if actor is None:
            return {"status": "no_actor", "project_id": project_id}
        project = session.get(ProductionProject, project_id)
        if project is None:
            return {"status": "not_found", "project_id": project_id}
        analysis = session.scalar(
            select(VideoAnalysis).where(
                VideoAnalysis.project_id == project.id,
                VideoAnalysis.analysis_version == (analysis_version or settings.analysis_version),
            )
        )
        if analysis is None:
            analysis = request_analysis(
                session, actor, project, rerun=rerun, analysis_version=analysis_version
            )
        result = execute_analysis(
            session,
            actor,
            analysis,
            LocalFilesystemStorage(Path(settings.local_storage_root)),
        )
        if result.status == "COMPLETED":
            # Analysis has no remaining human gate. Ranking is deterministic,
            # idempotent, and still pauses before any clip is rendered.
            generate_clip_opportunities.delay(str(result.id))
            generate_producer_recommendations.delay(str(project.id))
        return {"status": result.status, "analysis_id": str(result.id)}
    finally:
        session.close()


@celery_app.task(name="viralforge.process_accepted_source")
def process_accepted_source(project_id: str) -> dict[str, str]:
    """Advance an accepted source through download and analysis exactly once.

    This task performs no creative decision, does not render a clip, and does
    not publish. `download_project` and `request_analysis` are idempotent, so
    retry delivery cannot create a second source or analysis run.
    """
    from sqlalchemy import select

    from app.accounts.models import Role, RoleName, User, UserRole
    from app.analysis.service import request_analysis
    from app.ingestion.storage import LocalFilesystemStorage
    from app.production.models import ProductionProject
    from app.production.service import download_project

    session = next(get_session())
    try:
        actor = session.scalar(
            select(User.id)
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(User.is_active, Role.name.in_([RoleName.OWNER, RoleName.ADMIN]))
            .order_by(User.created_at)
        )
        if actor is None:
            return {"status": "no_actor", "project_id": project_id}
        project = session.get(ProductionProject, project_id)
        if project is None:
            return {"status": "not_found", "project_id": project_id}
        if project.status == "SOURCE_REJECTED":
            return {"status": "source_rejected", "project_id": project_id}
        project = download_project(
            session, actor, project, LocalFilesystemStorage(Path(settings.local_storage_root))
        )
        analysis = request_analysis(session, actor, project)
        run_video_analysis.delay(str(project.id), analysis_version=analysis.analysis_version)
        return {"status": "processing", "project_id": project_id}
    finally:
        session.close()


@celery_app.task(name="viralforge.generate_clip_opportunities")
def generate_clip_opportunities(analysis_id: str, rerun: bool = False) -> dict[str, str | int]:
    """Generate one idempotent, explainable opportunity ranking from stored analysis."""
    from sqlalchemy import select

    from app.accounts.models import Role, RoleName, User, UserRole
    from app.analysis.models import VideoAnalysis
    from app.opportunities.models import OpportunityGenerationRun
    from app.opportunities.service import (
        execute_opportunity_generation,
        request_opportunity_generation,
    )

    session = next(get_session())
    try:
        actor = session.scalar(
            select(User.id)
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(User.is_active, Role.name.in_([RoleName.OWNER, RoleName.ADMIN]))
            .order_by(User.created_at)
        )
        if actor is None:
            return {"status": "no_actor", "analysis_id": analysis_id}
        analysis = session.get(VideoAnalysis, analysis_id)
        if analysis is None:
            return {"status": "not_found", "analysis_id": analysis_id}
        run = session.scalar(
            select(OpportunityGenerationRun)
            .where(OpportunityGenerationRun.analysis_id == analysis.id)
            .order_by(OpportunityGenerationRun.generation_version.desc())
        )
        if run is None:
            run = request_opportunity_generation(session, actor, analysis, rerun=rerun)
        result = execute_opportunity_generation(session, actor, run)
        return {
            "status": result.status,
            "run_id": str(result.id),
            "opportunity_count": result.opportunity_count,
        }
    finally:
        session.close()


@celery_app.task(name="viralforge.render_approved_opportunity")
def render_approved_opportunity(opportunity_id: str) -> dict[str, str]:
    """Render one human-approved opportunity and prepare its private preview.

    The approval itself remains the creative gate. This task only performs the
    persisted mechanical work and is safe when Celery redelivers it.
    """
    from sqlalchemy import select

    from app.accounts.models import Role, RoleName, User, UserRole
    from app.ingestion.storage import LocalFilesystemStorage
    from app.opportunities.models import ClipOpportunity, OpportunityReviewStatus
    from app.opportunities.service import generate_approved_opportunity

    session = next(get_session())
    try:
        actor = session.scalar(
            select(User.id)
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(User.is_active, Role.name.in_([RoleName.OWNER, RoleName.ADMIN]))
            .order_by(User.created_at)
        )
        opportunity = session.get(ClipOpportunity, opportunity_id)
        if actor is None:
            return {"status": "no_actor", "opportunity_id": opportunity_id}
        if opportunity is None:
            return {"status": "not_found", "opportunity_id": opportunity_id}
        if opportunity.review_status != OpportunityReviewStatus.APPROVED:
            return {"status": "not_approved", "opportunity_id": opportunity_id}
        clip = generate_approved_opportunity(
            session, actor, opportunity, LocalFilesystemStorage(Path(settings.local_storage_root))
        )
        if clip is None:
            return {"status": "already_rendered", "opportunity_id": opportunity_id}
        if clip.render_status == "SUCCEEDED" and settings.preview_proxy_enabled:
            generate_preview_proxy_task.delay(str(clip.id))
        if clip.render_status == "SUCCEEDED":
            generate_clip_quality_report.delay(str(clip.id))
        return {"status": clip.render_status, "clip_id": str(clip.id)}
    finally:
        session.close()


@celery_app.task(name="viralforge.generate_content_package")
def generate_content_package(clip_id: str, rerun: bool = False) -> dict[str, str | int]:
    """Generate a review-only content package; this never queues or publishes a clip."""
    from sqlalchemy import select

    from app.accounts.models import Role, RoleName, User, UserRole
    from app.content_packages.models import ContentPackage
    from app.content_packages.service import (
        execute_content_package_generation,
        request_content_package_generation,
    )
    from app.production.models import ProductionClip

    session = next(get_session())
    try:
        actor = session.scalar(
            select(User.id)
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(User.is_active, Role.name.in_([RoleName.OWNER, RoleName.ADMIN]))
            .order_by(User.created_at)
        )
        if actor is None:
            return {"status": "no_actor", "clip_id": clip_id}
        clip = session.get(ProductionClip, clip_id)
        if clip is None:
            return {"status": "not_found", "clip_id": clip_id}
        package = session.scalar(
            select(ContentPackage)
            .where(ContentPackage.clip_id == clip.id)
            .order_by(ContentPackage.generation_version.desc())
        )
        if package is None:
            package = request_content_package_generation(session, actor, clip, rerun=rerun)
        result = execute_content_package_generation(session, actor, package)
        return {
            "status": result.status,
            "content_package_id": str(result.id),
            "generation_version": result.generation_version,
        }
    finally:
        session.close()


@celery_app.task(name="viralforge.generate_producer_recommendations")
def generate_producer_recommendations(project_id: str) -> dict[str, str | int]:
    """Persist advisory producer decisions without advancing the project."""
    from sqlalchemy import select

    from app.accounts.models import Role, RoleName, User, UserRole
    from app.producer.service import generate_project_recommendations
    from app.production.models import ProductionProject

    session = next(get_session())
    try:
        actor = session.scalar(select(User.id).join(UserRole, UserRole.user_id == User.id).join(Role, Role.id == UserRole.role_id).where(User.is_active, Role.name.in_([RoleName.OWNER, RoleName.ADMIN])).order_by(User.created_at))
        project = session.get(ProductionProject, project_id)
        if project is None:
            return {"status": "not_found", "project_id": project_id}
        items = generate_project_recommendations(session, actor, project)
        return {"status": "ok", "project_id": project_id, "recommendation_count": len(items)}
    finally:
        session.close()


@celery_app.task(name="viralforge.generate_clip_quality_report")
def generate_clip_quality_report(clip_id: str) -> dict[str, str | int | float]:
    """Persist a quality report for an already-rendered clip; never publish it."""
    from sqlalchemy import select

    from app.accounts.models import Role, RoleName, User, UserRole
    from app.producer.service import (
        generate_clip_quality_report as build_report,
    )
    from app.producer.service import (
        generate_clip_recommendations,
    )
    from app.production.models import ProductionClip

    session = next(get_session())
    try:
        actor = session.scalar(select(User.id).join(UserRole, UserRole.user_id == User.id).join(Role, Role.id == UserRole.role_id).where(User.is_active, Role.name.in_([RoleName.OWNER, RoleName.ADMIN])).order_by(User.created_at))
        clip = session.get(ProductionClip, clip_id)
        if clip is None:
            return {"status": "not_found", "clip_id": clip_id}
        report = build_report(session, actor, clip)
        generate_clip_recommendations(session, actor, clip)
        return {"status": "ok", "clip_id": clip_id, "overall_readiness": report.overall_readiness}
    finally:
        session.close()


@celery_app.task(name="viralforge.evaluate_producer_predictions")
def evaluate_producer_predictions() -> dict[str, int | str]:
    """Compare stored estimates with official analytics only; no automatic tuning."""
    from app.producer.service import evaluate_predictions

    session = next(get_session())
    try:
        return {"status": "ok", "created": evaluate_predictions(session)}
    finally:
        session.close()


@celery_app.task(name="viralforge.execute_publish_request")
def execute_publish_request(request_id: str) -> dict[str, str | int]:
    """Execute one already-confirmed request; approvals alone never enqueue this task."""
    from app.publishing.service import PublishingError, execute_publish

    session = next(get_session())
    try:
        try:
            request = execute_publish(session, uuid.UUID(request_id))
        except PublishingError as error:
            return {"status": "blocked", "code": error.code}
        return {
            "status": request.status,
            "request_id": str(request.id),
            "progress": request.upload_progress_percent,
        }
    finally:
        session.close()


@celery_app.task(name="viralforge.execute_tiktok_publish_request")
def execute_tiktok_publish_request(request_id: str) -> dict[str, str | int]:
    """Bounded, explicitly-confirmed TikTok transfer; never invoked by approval alone."""
    from app.publishing.service import PublishingError, execute_tiktok_publish

    session = next(get_session())
    try:
        try:
            request = execute_tiktok_publish(session, uuid.UUID(request_id))
        except PublishingError as error:
            return {"status": "blocked", "code": error.code}
        return {"status": request.status, "request_id": str(request.id), "progress": request.upload_progress_percent}
    finally:
        session.close()


@celery_app.task(name="viralforge.refresh_tiktok_publish_status")
def refresh_tiktok_publish_status(request_id: str) -> dict[str, str | int]:
    from app.publishing.service import PublishingError, refresh_tiktok_status

    session = next(get_session())
    try:
        try:
            request = refresh_tiktok_status(session, uuid.UUID(request_id))
        except PublishingError as error:
            return {"status": "blocked", "code": error.code}
        return {"status": request.status, "request_id": str(request.id), "progress": request.upload_progress_percent}
    finally:
        session.close()


@celery_app.task(name="viralforge.refresh_due_tiktok_publish_requests")
def refresh_due_tiktok_publish_requests() -> dict[str, int | str]:
    """One bounded status pass; a scheduler never loops on a remote publish."""
    from sqlalchemy import select

    from app.publishing.models import PublishRequest, PublishRequestStatus
    from app.publishing.service import refresh_tiktok_status

    if not settings.tiktok_enabled:
        return {"status": "disabled", "processed": 0}
    session = next(get_session())
    try:
        requests = list(session.scalars(select(PublishRequest).where(PublishRequest.provider_mode.in_(["DRAFT_UPLOAD", "DIRECT_POST"]), PublishRequest.status.in_([PublishRequestStatus.PROCESSING, PublishRequestStatus.UNKNOWN_REMOTE_OUTCOME])).order_by(PublishRequest.updated_at).limit(10)))
        for request in requests:
            try:
                refresh_tiktok_status(session, request.id)
            except Exception:  # A single provider error must not stall the bounded scheduler tick.
                continue
        return {"status": "ok", "processed": len(requests)}
    finally:
        session.close()


@celery_app.task(name="viralforge.refresh_tiktok_credentials")
def refresh_tiktok_credentials() -> dict[str, int | str]:
    """Bounded refresh pass; encrypted-store replacement is atomic per destination."""
    if not settings.tiktok_enabled:
        return {"status": "disabled", "refreshed": 0}
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.brands.models import DestinationAccount
    from app.publishing.models import PublishingAccountConnection
    from app.publishing.service import PublishingError, refresh_tiktok_connection

    session = next(get_session())
    try:
        due_at = (datetime.now(UTC) + timedelta(seconds=settings.tiktok_credential_refresh_window_seconds)).isoformat()
        accounts = list(
            session.scalars(
                select(DestinationAccount)
                .join(
                    PublishingAccountConnection,
                    PublishingAccountConnection.destination_account_id == DestinationAccount.id,
                )
                .where(
                    DestinationAccount.provider == "TIKTOK",
                    DestinationAccount.is_active.is_(True),
                    PublishingAccountConnection.connection_state == "CONNECTED",
                    PublishingAccountConnection.credential_expires_at.is_not(None),
                    PublishingAccountConnection.credential_expires_at <= due_at,
                )
                .order_by(PublishingAccountConnection.credential_expires_at)
                .limit(10)
            )
        )
        refreshed = 0
        for account in accounts:
            try:
                refresh_tiktok_connection(session, None, account.id)
                refreshed += 1
            except PublishingError:
                continue
        return {"status": "ok", "refreshed": refreshed}
    finally:
        session.close()


@celery_app.task(name="viralforge.execute_due_publish_requests")
def execute_due_publish_requests() -> dict[str, int | str]:
    """Bounded scheduler tick for human-confirmed scheduled uploads only."""
    from datetime import UTC, datetime

    from sqlalchemy import and_, or_, select

    from app.publishing.models import PublishRequest, PublishRequestStatus
    from app.publishing.service import execute_publish

    if not settings.publishing_enabled:
        return {"status": "disabled", "processed": 0}
    session = next(get_session())
    try:
        now = datetime.now(UTC).isoformat()
        requests = list(
            session.scalars(
                select(PublishRequest)
                .where(
                    or_(
                        and_(
                            PublishRequest.status == PublishRequestStatus.SCHEDULED,
                            PublishRequest.scheduled_for <= now,
                        ),
                        and_(
                            PublishRequest.status == PublishRequestStatus.FAILED,
                            PublishRequest.next_attempt_at.is_not(None),
                            PublishRequest.next_attempt_at <= now,
                        ),
                    )
                )
                .order_by(PublishRequest.next_attempt_at, PublishRequest.scheduled_for)
                .limit(10)
            )
        )
        for request in requests:
            execute_publish(session, request.id)
        return {"status": "ok", "processed": len(requests)}
    finally:
        session.close()


@celery_app.task(name="viralforge.refresh_published_analytics")
def refresh_published_analytics() -> dict[str, int | str]:
    """Bounded, read-only analytics refresh; disabled unless explicitly enabled."""
    from app.analytics.service import refresh_brand

    if not settings.analytics_enabled or not settings.analytics_youtube_enabled:
        return {"status": "disabled", "snapshots": 0}
    session = next(get_session())
    try:
        run = refresh_brand(session, None)
        return {"status": run.status, "snapshots": run.snapshot_count}
    finally:
        session.close()


@celery_app.task(name="viralforge.cleanup_expired_media")
def cleanup_expired_media_task(dry_run: bool | None = None) -> dict[str, int | bool | str]:
    """Bounded cleanup tick; deployment schedules it with Celery Beat or cron."""
    from app.ingestion.storage import LocalFilesystemStorage
    from app.media_preview.service import cleanup_expired_media

    if not settings.cleanup_enabled:
        return {"status": "disabled", "deleted": 0}
    session = next(get_session())
    try:
        result = cleanup_expired_media(
            session,
            LocalFilesystemStorage(Path(settings.local_storage_root)),
            dry_run=dry_run,
        )
        return {"status": "ok", **result.__dict__}
    finally:
        session.close()


@celery_app.task(name="viralforge.generate_preview_proxy")
def generate_preview_proxy_task(clip_id: str) -> dict[str, str | bool]:
    """Optional proxy work does not alter the rendered clip or publishing path."""
    from sqlalchemy import select

    from app.accounts.models import Role, RoleName, User, UserRole
    from app.ingestion.storage import LocalFilesystemStorage
    from app.media_preview.service import generate_proxy
    from app.production.models import ProductionClip

    session = next(get_session())
    try:
        actor = session.scalar(
            select(User.id)
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(User.is_active, Role.name.in_([RoleName.OWNER, RoleName.ADMIN]))
            .order_by(User.created_at)
        )
        clip = session.get(ProductionClip, clip_id)
        if actor is None or clip is None:
            return {"generated": False, "status": "not_found"}
        asset = generate_proxy(
            session, actor, clip, LocalFilesystemStorage(Path(settings.local_storage_root))
        )
        return {"generated": asset is not None, "status": "ok"}
    finally:
        session.close()
