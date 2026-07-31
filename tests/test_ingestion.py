import uuid

import httpx
import pytest

from app.common.errors import DomainError
from app.ingestion.http import SafeOutboundHttpClient
from app.ingestion.service import submit_url
from app.ingestion.url import normalize_url
from tests.conftest import DEV_ACTOR_ID


def test_normalize_url_removes_tracking_preserves_identifiers():
    assert (
        normalize_url("HTTPS://Example.com:443/a//b/?utm_source=x&id=42#fragment")
        == "https://example.com/a/b?id=42"
    )


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/a",
        "http://localhost/a",
        "http://127.0.0.1/a",
        "https://user:pass@example.com/a",
    ],
)
def test_normalizer_blocks_unsafe_urls(url: str):
    with pytest.raises(DomainError):
        normalize_url(url)


def test_url_ingestion_is_idempotent_and_records_provenance(session):  # type: ignore[no-untyped-def]
    async def resolver(_: str) -> list[str]:
        return ["93.184.216.34"]

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/html"}, text="<title>Recorded story</title>"
        )

    key = str(uuid.uuid4())
    client = SafeOutboundHttpClient(resolver=resolver, transport=httpx.MockTransport(handler))
    first = submit_url(
        session,
        DEV_ACTOR_ID,
        "https://example.com/story?utm_campaign=x&id=7",
        key,
        http_client=client,
    )
    second = submit_url(
        session, DEV_ACTOR_ID, "https://example.com/story?id=7", key, http_client=client
    )
    assert first.id == second.id
    assert first.result_content_id is not None
