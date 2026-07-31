import asyncio
import uuid

import httpx
import pytest
from sqlalchemy import select

from app.audit.models import AuditEvent
from app.common.config import Settings
from app.content.models import ContentItem, ContentSource, ContentStatus, Platform
from app.ingestion.http import FetchErrorCategory, SafeFetchError, SafeOutboundHttpClient
from app.ingestion.metadata import extract_metadata
from app.ingestion.models import IngestionJob, IngestionStatus
from app.ingestion.service import submit_url
from app.sources.models import Source, SourcePolicy, SourceStatus
from tests.conftest import DEV_ACTOR_ID


async def public_resolver(_: str) -> list[str]:
    return ["93.184.216.34"]


def client_for(handler, resolver=public_resolver, **settings):  # type: ignore[no-untyped-def]
    return SafeOutboundHttpClient(
        settings=Settings(**settings), resolver=resolver, transport=httpx.MockTransport(handler)
    )


def test_safe_client_rejects_private_and_mixed_dns_answers():
    async def private_resolver(_: str) -> list[str]:
        return ["10.0.0.7"]

    async def mixed_resolver(_: str) -> list[str]:
        return ["93.184.216.34", "10.0.0.7"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, request=request)

    for resolver in (private_resolver, mixed_resolver):
        with pytest.raises(SafeFetchError) as error:
            asyncio.run(client_for(handler, resolver).fetch("https://public.example/story"))
        assert error.value.category is FetchErrorCategory.SSRF_BLOCKED


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1/a", "http://[::1]/a", "http://169.254.169.254/a"]
)
def test_safe_client_rejects_local_and_metadata_literals(url: str):
    with pytest.raises(SafeFetchError) as error:
        asyncio.run(client_for(lambda request: httpx.Response(200, request=request)).fetch(url))
    assert error.value.category is FetchErrorCategory.SSRF_BLOCKED


def test_safe_client_revalidates_relative_redirect_and_rejects_private_redirect():
    def good_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/final"}, request=request)
        return httpx.Response(
            200, headers={"content-type": "text/html"}, text="<title>Final</title>", request=request
        )

    result = asyncio.run(client_for(good_handler).fetch("https://public.example/start"))
    assert result.final_url == "https://public.example/final"
    assert result.redirects == ("https://public.example/start",)

    def private_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302, headers={"location": "http://127.0.0.1/private"}, request=request
        )

    with pytest.raises(SafeFetchError) as error:
        asyncio.run(client_for(private_handler).fetch("https://public.example/start"))
    assert error.value.category is FetchErrorCategory.SSRF_BLOCKED


def test_safe_client_enforces_response_type_and_actual_stream_limit():
    def binary(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "video/mp4"}, content=b"not-read", request=request
        )

    with pytest.raises(SafeFetchError) as error:
        asyncio.run(client_for(binary).fetch("https://public.example/video"))
    assert error.value.category is FetchErrorCategory.UNSUPPORTED_CONTENT_TYPE

    def oversized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/html"}, content=b"x" * 1_025, request=request
        )

    with pytest.raises(SafeFetchError) as error:
        asyncio.run(
            client_for(oversized, ingestion_http_max_response_bytes=1_024).fetch(
                "https://public.example/page"
            )
        )
    assert error.value.category is FetchErrorCategory.RESPONSE_TOO_LARGE

    def declared_oversized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html", "content-length": "1025"},
            request=request,
        )

    with pytest.raises(SafeFetchError) as error:
        asyncio.run(
            client_for(declared_oversized, ingestion_http_max_response_bytes=1_024).fetch(
                "https://public.example/declared"
            )
        )
    assert error.value.category is FetchErrorCategory.RESPONSE_TOO_LARGE


def test_safe_client_rejects_redirect_loops_and_limits():
    def loop_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/again"}, request=request)

    with pytest.raises(SafeFetchError) as error:
        asyncio.run(client_for(loop_handler).fetch("https://public.example/again"))
    assert error.value.category is FetchErrorCategory.REDIRECT_LOOP

    def chain_handler(request: httpx.Request) -> httpx.Response:
        target = "/two" if request.url.path == "/one" else "/three"
        return httpx.Response(302, headers={"location": target}, request=request)

    with pytest.raises(SafeFetchError) as error:
        asyncio.run(
            client_for(chain_handler, ingestion_http_max_redirects=1).fetch(
                "https://public.example/one"
            )
        )
    assert error.value.category is FetchErrorCategory.TOO_MANY_REDIRECTS


def test_metadata_extraction_has_deterministic_priorities_and_safe_canonical():
    metadata = extract_metadata(
        b"""<html lang='en'><head><title>HTML</title><link rel='canonical' href='/canonical?x=1'>
        <meta property='og:title' content='Open Graph'><meta name='twitter:title' content='Twitter'>
        <meta name='description' content='Plain'><meta property='og:description' content='Open description'>
        <meta name='author' content='Author'><meta property='article:published_time' content='2026-01-01'>
        <meta property='og:video' content='https://media.example/video.mp4'></head></html>""",
        "https://public.example/page",
        200,
        "text/html",
    )
    assert metadata.selected["title"] == "Open Graph"
    assert metadata.selected["description"] == "Open description"
    assert metadata.selected["canonical_url"] == "https://public.example/canonical?x=1"
    assert metadata.raw["og_video_url"] == "https://media.example/video.mp4"
    assert metadata.raw["published_at"] == "2026-01-01"


def test_metadata_parser_handles_malformed_encoding_duplicates_and_value_limits():
    oversized = "x" * 700
    metadata = extract_metadata(
        (
            b"<html><head><title>First</title>"
            + f"<meta property='og:title' content='{oversized}'>".encode()
            + b"<meta property='og:title' content='Ignored'></head><body><p\xff"
        ),
        "https://public.example/page",
        200,
        "text/html",
    )
    assert metadata.selected["title"] == "x" * 500
    assert metadata.raw["og_title"] == "x" * 500


def test_successful_workflow_records_provenance_lifecycle_and_audit(session):  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<title>Safe title</title><meta name='description' content='Safe description'>",
            request=request,
        )

    job = submit_url(
        session,
        DEV_ACTOR_ID,
        "https://public.example/story",
        str(uuid.uuid4()),
        http_client=client_for(handler),
    )
    item = session.get(ContentItem, job.result_content_id)
    assert job.status is IngestionStatus.SUCCEEDED
    assert item is not None and item.status is ContentStatus.SOURCE_VERIFICATION_REQUIRED
    assert (
        session.scalar(select(ContentSource.content_id).where(ContentSource.content_id == item.id))
        == item.id
    )
    events = list(session.scalars(select(AuditEvent).where(AuditEvent.entity_id == job.id)))
    assert {event.event_name for event in events} >= {"ingestion.url.succeeded"}


def test_failed_fetch_persists_job_without_content(session):  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/zip"}, request=request)

    job = submit_url(
        session,
        DEV_ACTOR_ID,
        "https://public.example/archive",
        str(uuid.uuid4()),
        http_client=client_for(handler),
    )
    persisted = session.get(IngestionJob, job.id)
    assert persisted is not None and persisted.status is IngestionStatus.FAILED
    assert persisted.error_category == FetchErrorCategory.UNSUPPORTED_CONTENT_TYPE.value
    assert session.scalar(select(ContentItem.id)) is None


def test_canonical_duplicate_and_retryable_failure_are_recorded(session):  # type: ignore[no-untyped-def]
    def first_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<link rel='canonical' href='https://public.example/canonical'><title>First</title>",
            request=request,
        )

    first = submit_url(
        session,
        DEV_ACTOR_ID,
        "https://public.example/first",
        str(uuid.uuid4()),
        http_client=client_for(first_handler),
    )
    assert first.result_content_id is not None

    def duplicate_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<link rel='canonical' href='https://public.example/canonical'><title>Second</title>",
            request=request,
        )

    duplicate = submit_url(
        session,
        DEV_ACTOR_ID,
        "https://public.example/second",
        str(uuid.uuid4()),
        http_client=client_for(duplicate_handler),
    )
    assert duplicate.status is IngestionStatus.SUCCEEDED
    assert duplicate.result_content_id == first.result_content_id
    duplicate_event = session.scalar(
        select(AuditEvent).where(
            AuditEvent.entity_id == duplicate.id, AuditEvent.event_name == "duplicate.detected"
        )
    )
    assert duplicate_event is not None
    assert duplicate_event.payload == {
        "outcome": "CANONICAL_URL_DUPLICATE",
        "content_id": str(first.result_content_id),
    }

    def unavailable_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)

    retry = submit_url(
        session,
        DEV_ACTOR_ID,
        "https://public.example/unavailable",
        str(uuid.uuid4()),
        http_client=client_for(unavailable_handler),
    )
    assert retry.status is IngestionStatus.RETRY_SCHEDULED
    assert retry.error_category == FetchErrorCategory.REMOTE_SERVER_ERROR.value


def test_archived_source_policy_rejects_submission_and_finishes_job(session):  # type: ignore[no-untyped-def]
    source = Source(
        platform=Platform.MANUAL,
        normalized_url="https://public.example/archived",
        status=SourceStatus.ARCHIVED,
    )
    session.add(source)
    session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, request=request)

    job = submit_url(
        session,
        DEV_ACTOR_ID,
        "https://public.example/archived",
        str(uuid.uuid4()),
        source_id=source.id,
        http_client=client_for(handler),
    )
    assert job.status is IngestionStatus.FAILED
    assert job.error_category == FetchErrorCategory.POLICY_VIOLATION.value


def test_domain_policy_rejects_before_network_retrieval(session):  # type: ignore[no-untyped-def]
    source = Source(platform=Platform.MANUAL, normalized_url="https://public.example/blocked")
    session.add(source)
    session.flush()
    session.add(
        SourcePolicy(
            source_id=source.id,
            policy_version="test",
            blocked_domains=["public.example"],
            request_timeout_seconds=1,
            redirect_limit=0,
        )
    )
    session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"network retrieval was attempted for {request.url}")

    job = submit_url(
        session,
        DEV_ACTOR_ID,
        "https://public.example/blocked",
        str(uuid.uuid4()),
        source_id=source.id,
        http_client=client_for(handler),
    )
    assert job.status is IngestionStatus.FAILED
    assert job.error_category == FetchErrorCategory.POLICY_VIOLATION.value
