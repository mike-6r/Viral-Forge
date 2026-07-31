import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.common.errors import DomainError, PreconditionError
from app.content.lifecycle import transition
from app.content.models import ContentItem, ContentSource, ContentStatus, Platform
from app.ingestion.http import FetchErrorCategory, SafeFetchError, SafeOutboundHttpClient
from app.ingestion.metadata import ExtractedMetadata, extract_metadata
from app.ingestion.models import DuplicateOutcome, IngestionJob, IngestionMethod, IngestionStatus
from app.ingestion.policy import enforce_url_policy, retrieval_controls
from app.ingestion.url import normalize_url
from app.sources.models import Source, SourcePolicy, SourceStatus, SourceType


def _latest_policy(session: Session, source: Source) -> SourcePolicy | None:
    return session.scalar(
        select(SourcePolicy)
        .where(SourcePolicy.source_id == source.id)
        .order_by(SourcePolicy.created_at.desc())
    )


def _duplicate_content(session: Session, url: str) -> uuid.UUID | None:
    return session.scalar(
        select(ContentSource.content_id).where(ContentSource.source_url == url).limit(1)
    )


def _record_duplicate(
    session: Session,
    job: IngestionJob,
    actor_id: uuid.UUID,
    correlation_id: str | None,
    content_id: uuid.UUID,
    outcome: DuplicateOutcome,
) -> None:
    job.status = IngestionStatus.SUCCEEDED
    job.result_content_id = content_id
    job.completed_at = datetime.now(UTC)
    job.progress = 100
    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="ingestion_job",
            entity_id=job.id,
            event_name="duplicate.detected",
            correlation_id=correlation_id,
            payload={"outcome": outcome.value, "content_id": str(content_id)},
        )
    )


def _fail_job(
    session: Session,
    job: IngestionJob,
    actor_id: uuid.UUID,
    correlation_id: str | None,
    error: SafeFetchError,
) -> None:
    job.error_category = error.category.value
    job.error_message = str(error)
    job.completed_at = datetime.now(UTC)
    if error.retryable and job.attempts < job.max_attempts:
        job.status = IngestionStatus.RETRY_SCHEDULED
        job.retry_at = datetime.now(UTC) + timedelta(minutes=1)
    else:
        job.status = IngestionStatus.FAILED
    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="ingestion_job",
            entity_id=job.id,
            event_name="ingestion.url.failed",
            correlation_id=correlation_id,
            payload={"category": error.category.value, "retryable": error.retryable},
        )
    )


def _source_metadata(
    submitted_url: str,
    normalized_url: str,
    result_url: str,
    metadata: ExtractedMetadata,
) -> dict[str, object]:
    return {
        "manual_url_ingestion": {
            "submitted_url": submitted_url,
            "normalized_submitted_url": normalized_url,
            "final_url": result_url,
            "canonical_url": metadata.selected["canonical_url"],
            "canonical_url_unverified": metadata.selected["canonical_url"] is not None,
            "raw_metadata": metadata.raw,
            "selected_metadata": metadata.selected,
            "warnings": list(metadata.warnings),
        }
    }


def submit_url(
    session: Session,
    actor_id: uuid.UUID,
    submitted_url: str,
    idempotency_key: str,
    correlation_id: str | None = None,
    source_id: uuid.UUID | None = None,
    notes: str | None = None,
    http_client: SafeOutboundHttpClient | None = None,
) -> IngestionJob:
    """Perform one synchronous manual URL retrieval using the async network boundary.

    FastAPI dispatches this synchronous service in a worker thread.  Future
    Celery work can call the same boundary without changing workflow semantics.
    """
    existing = session.scalar(
        select(IngestionJob).where(IngestionJob.idempotency_key == idempotency_key)
    )
    if existing is not None:
        return existing
    normalized_url = normalize_url(submitted_url)
    source = session.get(Source, source_id) if source_id is not None else None
    if source_id is not None and source is None:
        raise PreconditionError("source was not found")
    if source is None:
        source = session.scalar(select(Source).where(Source.normalized_url == normalized_url))
    if source is None:
        source = Source(
            platform=Platform.MANUAL,
            normalized_url=normalized_url,
            source_type=SourceType.MANUAL_URL,
            status=SourceStatus.PENDING_REVIEW,
        )
        session.add(source)
        session.flush()
    job = IngestionJob(
        method=IngestionMethod.MANUAL_URL,
        status=IngestionStatus.RUNNING,
        actor_id=actor_id,
        source_id=source.id,
        requested_url=submitted_url,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        attempts=1,
        started_at=datetime.now(UTC),
        heartbeat_at=datetime.now(UTC),
    )
    session.add(job)
    session.flush()
    # Preserve the submission before any untrusted network operation.  A later
    # rollback can therefore remove partial content while retaining this job.
    job_id = job.id
    session.commit()
    try:
        policy = _latest_policy(session, source)
        enforce_url_policy(source, policy, normalized_url, automated=False)
        controls = retrieval_controls(policy)
        duplicate = _duplicate_content(session, normalized_url)
        if duplicate is not None:
            _record_duplicate(
                session,
                job,
                actor_id,
                correlation_id,
                duplicate,
                DuplicateOutcome.EXACT_URL_DUPLICATE,
            )
            session.commit()
            return job
        client = http_client or SafeOutboundHttpClient()

        def redirect_policy(destination: str) -> None:
            enforce_url_policy(source, policy, destination, automated=False)

        result = asyncio.run(
            client.fetch(
                normalized_url,
                correlation_id,
                redirect_policy,
                controls.total_timeout_seconds,
                controls.max_redirects,
            )
        )
        metadata = extract_metadata(
            result.body, result.final_url, result.status_code, result.content_type
        )
        canonical = metadata.selected["canonical_url"]
        if canonical is not None:
            canonical_duplicate = _duplicate_content(session, canonical)
            if canonical_duplicate is not None:
                _record_duplicate(
                    session,
                    job,
                    actor_id,
                    correlation_id,
                    canonical_duplicate,
                    DuplicateOutcome.CANONICAL_URL_DUPLICATE,
                )
                source.provider_metadata = _source_metadata(
                    submitted_url, normalized_url, result.final_url, metadata
                )
                session.commit()
                return job
        source.provider_metadata = _source_metadata(
            submitted_url, normalized_url, result.final_url, metadata
        )
        title = metadata.selected["title"] or result.final_url
        item = ContentItem(
            title=title,
            description=metadata.selected["description"],
            status=ContentStatus.DISCOVERED,
            source_provenance_complete=True,
        )
        session.add(item)
        session.flush()
        session.add(
            ContentSource(
                content_id=item.id, source_id=source.id, source_url=canonical or result.final_url
            )
        )
        transition(
            session, item, ContentStatus.IMPORTED, actor_id, "metadata retrieved", correlation_id
        )
        transition(
            session,
            item,
            ContentStatus.SOURCE_VERIFICATION_REQUIRED,
            actor_id,
            "manual URL source requires verification",
            correlation_id,
        )
        job.status = IngestionStatus.SUCCEEDED
        job.progress = 100
        job.result_content_id = item.id
        job.completed_at = datetime.now(UTC)
        session.add_all(
            [
                AuditEvent(
                    actor_id=actor_id,
                    entity_type="ingestion_job",
                    entity_id=job.id,
                    event_name="ingestion.url.succeeded",
                    correlation_id=correlation_id,
                    payload={
                        "normalized_url": normalized_url,
                        "final_url": result.final_url,
                        "canonical_url": canonical,
                        "notes_provided": notes is not None,
                    },
                ),
                AuditEvent(
                    actor_id=actor_id,
                    entity_type="content_item",
                    entity_id=item.id,
                    event_name="content.ingested",
                    correlation_id=correlation_id,
                    payload={"source_id": str(source.id), "status": item.status.value},
                ),
            ]
        )
        session.commit()
        return job
    except SafeFetchError as error:
        session.rollback()
        job = session.get(IngestionJob, job_id) or job
        _fail_job(session, job, actor_id, correlation_id, error)
        session.commit()
        return job
    except DomainError:
        session.rollback()
        job = session.get(IngestionJob, job_id) or job
        safe_error = SafeFetchError(
            FetchErrorCategory.POLICY_VIOLATION, "source policy rejected the request"
        )
        _fail_job(session, job, actor_id, correlation_id, safe_error)
        session.commit()
        return job


def change_source_status(
    session: Session,
    source: Source,
    target: SourceStatus,
    actor_id: uuid.UUID,
    correlation_id: str | None = None,
) -> Source:
    source.status = target
    session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type="source",
            entity_id=source.id,
            event_name=f"source.{target.value.lower()}",
            correlation_id=correlation_id,
        )
    )
    return source
