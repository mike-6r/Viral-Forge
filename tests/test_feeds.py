import asyncio
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from app.content.models import ContentItem, ContentStatus, Platform
from app.ingestion.feeds import parse_feed, register_feed, run_feed
from app.ingestion.http import SafeOutboundHttpClient
from app.ingestion.models import FeedEntry, IngestionStatus
from app.sources.models import Source, SourcePolicy, SourceStatus, SourceType
from tests.conftest import DEV_ACTOR_ID

RSS = b"""<?xml version='1.0'?><rss version='2.0'><channel><title>News</title><description>Latest</description><link>https://public.example</link><item><guid>one</guid><title>First</title><link>https://public.example/one</link><description>Summary</description></item></channel></rss>"""
ATOM = b"""<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'><title>News</title><entry><id>atom-one</id><title>First</title><link href='https://public.example/one'/><summary>Summary</summary></entry></feed>"""


async def resolver(_: str) -> list[str]:
    return ["93.184.216.34"]


def client(body: bytes, content_type: str = "application/rss+xml") -> SafeOutboundHttpClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304, request=request)
        return httpx.Response(
            200,
            headers={"content-type": content_type, "etag": '"v1"'},
            content=body,
            request=request,
        )

    return SafeOutboundHttpClient(resolver=resolver, transport=httpx.MockTransport(handler))


def active_source(session):  # type: ignore[no-untyped-def]
    source = Source(
        platform=Platform.MANUAL,
        normalized_url="https://public.example/source",
        source_type=SourceType.RSS_FEED,
        status=SourceStatus.ACTIVE,
    )
    session.add(source)
    session.flush()
    session.add(
        SourcePolicy(
            source_id=source.id, policy_version="test", permitted_methods=["RSS_FEED", "ATOM_FEED"]
        )
    )
    session.commit()
    return source


def test_parses_rss_atom_and_rejects_unsafe_xml():
    assert parse_feed(RSS)[0].value == "RSS_FEED"
    assert parse_feed(ATOM)[0].value == "ATOM_FEED"
    try:
        parse_feed(b"<!DOCTYPE foo [<!ENTITY xxe SYSTEM 'file:///x'>]><rss>&xxe;</rss>")
    except Exception:
        pass
    else:
        raise AssertionError("unsafe XML was accepted")


def test_register_and_run_feed_is_idempotent_with_conditional_request(session):  # type: ignore[no-untyped-def]
    source = active_source(session)
    safe = client(RSS)
    feed = asyncio.run(
        register_feed(session, DEV_ACTOR_ID, source.id, "https://public.example/feed", safe)
    )
    assert feed.status == "ACTIVE"
    feed.etag = None
    session.commit()
    first = asyncio.run(run_feed(session, DEV_ACTOR_ID, feed.id, safe))
    assert first.status is IngestionStatus.SUCCEEDED
    assert session.scalar(select(ContentItem.id)) is not None
    feed.last_checked_at = datetime.now(UTC) - timedelta(hours=2)
    session.commit()
    second = asyncio.run(run_feed(session, DEV_ACTOR_ID, feed.id, safe))
    assert second.status is IngestionStatus.SUCCEEDED
    assert len(list(session.scalars(select(FeedEntry)))) == 1
    assert session.scalar(select(ContentItem.status)) is ContentStatus.SOURCE_VERIFICATION_REQUIRED


def test_atom_registration_and_feed_provenance(session):  # type: ignore[no-untyped-def]
    source = active_source(session)
    feed = asyncio.run(
        register_feed(
            session,
            DEV_ACTOR_ID,
            source.id,
            "https://public.example/atom",
            client(ATOM, "application/atom+xml"),
        )
    )
    feed.etag = None
    session.commit()
    job = asyncio.run(
        run_feed(session, DEV_ACTOR_ID, feed.id, client(ATOM, "application/atom+xml"))
    )
    assert job.status is IngestionStatus.SUCCEEDED
    entry = session.scalar(select(FeedEntry))
    assert entry is not None and entry.entry_guid == "atom-one"
