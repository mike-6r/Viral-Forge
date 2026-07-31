from dataclasses import dataclass
from urllib.parse import urlsplit

from app.common.errors import PreconditionError
from app.ingestion.models import IngestionMethod
from app.sources.models import Source, SourcePolicy, SourceStatus


@dataclass(frozen=True)
class RetrievalControls:
    total_timeout_seconds: int | None
    max_redirects: int | None


def retrieval_controls(policy: SourcePolicy | None) -> RetrievalControls:
    if policy is None:
        return RetrievalControls(total_timeout_seconds=None, max_redirects=None)
    return RetrievalControls(
        total_timeout_seconds=policy.request_timeout_seconds,
        max_redirects=policy.redirect_limit,
    )


def enforce_url_policy(
    source: Source,
    policy: SourcePolicy | None,
    normalized_url: str,
    automated: bool,
    method: IngestionMethod = IngestionMethod.MANUAL_URL,
) -> None:
    host = (urlsplit(normalized_url).hostname or "").lower()
    if source.status is SourceStatus.BLOCKED:
        raise PreconditionError("source is blocked")
    if source.status in {SourceStatus.PAUSED, SourceStatus.REJECTED, SourceStatus.ARCHIVED}:
        raise PreconditionError("source is inactive")
    if automated and source.status is not SourceStatus.ACTIVE:
        raise PreconditionError("automated ingestion requires an active source")
    if policy is None:
        return
    if host in set(policy.blocked_domains or []):
        raise PreconditionError("domain is blocked by source policy")
    allowed = set(policy.allowed_domains or [])
    if allowed and host not in allowed:
        raise PreconditionError("domain is not allowed by source policy")
    permitted_methods = set(policy.permitted_methods or [])
    if permitted_methods and method.value not in permitted_methods:
        raise PreconditionError("ingestion method is not permitted by source policy")


def enforce_upload_policy(
    source: Source,
    policy: SourcePolicy | None,
    detected_media_type: str | None = None,
    size_bytes: int | None = None,
) -> None:
    if source.status is SourceStatus.BLOCKED:
        raise PreconditionError("source is blocked")
    if source.status in {SourceStatus.PAUSED, SourceStatus.REJECTED, SourceStatus.ARCHIVED}:
        raise PreconditionError("source is inactive")
    if policy is None:
        return
    methods = set(policy.permitted_methods or [])
    if methods and IngestionMethod.MANUAL_UPLOAD.value not in methods:
        raise PreconditionError("manual uploads are not permitted by source policy")
    if size_bytes is not None and size_bytes > policy.max_file_size_bytes:
        raise PreconditionError("upload exceeds source policy size limit")
    allowed_types = set(policy.permitted_media_types or [])
    if (
        detected_media_type is not None
        and allowed_types
        and detected_media_type not in allowed_types
    ):
        raise PreconditionError("media type is not permitted by source policy")
