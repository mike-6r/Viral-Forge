import httpx

from app.common.db import get_session
from app.content.models import Platform
from app.ingestion.feeds import get_feed_client
from app.ingestion.http import SafeOutboundHttpClient
from app.sources.models import Source, SourcePolicy, SourceStatus, SourceType
from tests.conftest import DEV_ACTOR_ID

RSS = b"""<?xml version='1.0'?><rss version='2.0'><channel><title>News</title><item><guid>one</guid><title>First</title><link>https://public.example/one</link></item></channel></rss>"""


async def resolver(_: str) -> list[str]:
    return ["93.184.216.34"]


def safe_client() -> SafeOutboundHttpClient:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "application/rss+xml", "etag": '"v1"'}, content=RSS
        )

    return SafeOutboundHttpClient(resolver=resolver, transport=httpx.MockTransport(handler))


def headers() -> dict[str, str]:
    return {"X-Development-Actor": str(DEV_ACTOR_ID)}


def test_feed_api_lifecycle(client, session):  # type: ignore[no-untyped-def]
    source = Source(
        platform=Platform.MANUAL,
        normalized_url="https://public.example/source-api",
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
    client.app.dependency_overrides[get_feed_client] = safe_client
    payload = {
        "source_id": str(source.id),
        "feed_url": "https://public.example/api-feed",
        "polling_interval_seconds": 60,
        "idempotency_key": "feed-registration-key",
    }
    response = client.post("/api/v1/feeds", headers=headers(), json=payload)
    assert response.status_code == 201, response.text
    feed_id = response.json()["id"]
    assert client.post("/api/v1/feeds", headers=headers(), json=payload).status_code == 200
    assert client.get("/api/v1/feeds", headers=headers()).json()["total"] == 1
    feed = client.get(f"/api/v1/feeds/{feed_id}", headers=headers()).json()
    assert (
        client.patch(
            f"/api/v1/feeds/{feed_id}",
            headers=headers(),
            json={"max_items_per_run": 1, "version_id": feed["version_id"]},
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"/api/v1/feeds/{feed_id}",
            headers=headers(),
            json={"max_items_per_run": 1, "version_id": feed["version_id"]},
        ).status_code
        == 409
    )
    run = client.post(f"/api/v1/feeds/{feed_id}/run", headers=headers(), json={})
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "SUCCEEDED"
    assert (
        client.post(f"/api/v1/feeds/{feed_id}/run", headers=headers(), json={}).status_code == 409
    )
    assert client.get(f"/api/v1/feeds/{feed_id}/entries", headers=headers()).json()["total"] == 1
    assert client.get(f"/api/v1/feeds/{feed_id}/runs", headers=headers()).json()["total"] == 1
    assert (
        client.post(f"/api/v1/feeds/{feed_id}/pause", headers=headers()).json()["status"]
        == "PAUSED"
    )
    assert (
        client.post(f"/api/v1/feeds/{feed_id}/activate", headers=headers()).json()["status"]
        == "ACTIVE"
    )
    assert (
        client.post(
            f"/api/v1/feeds/{feed_id}/block", headers=headers(), json={"reason": "controlled test"}
        ).json()["status"]
        == "BLOCKED"
    )
    client.app.dependency_overrides.pop(get_feed_client, None)
    client.app.dependency_overrides.pop(get_session, None)
