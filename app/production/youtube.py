"""Official YouTube Data API discovery boundary; no search-page scraping."""

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

import httpx

from app.common.config import Settings, get_settings
from app.production.service import ProductionError


@dataclass(frozen=True)
class YouTubeVideo:
    video_id: str
    title: str
    channel_title: str
    thumbnail_url: str | None
    published_at: datetime | None
    duration: str | None
    view_count: int | None

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


@dataclass(frozen=True)
class YouTubeChannel:
    """Public channel metadata returned by the official YouTube Data API."""

    channel_id: str
    title: str
    url: str
    thumbnail_url: str | None
    video_count: int | None
    latest_upload_title: str | None


def youtube_channel_reference(value: str) -> tuple[str, str]:
    """Return an official channel-id or handle reference without scraping."""
    clean = value.strip()
    if not clean:
        raise ProductionError("YOUTUBE_CHANNEL_REQUIRED", "a YouTube channel URL, handle, or ID is required")
    parsed = urlparse(clean if "://" in clean else f"https://{clean}")
    if "://" in clean and parsed.hostname not in {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
    }:
        raise ProductionError(
            "YOUTUBE_CHANNEL_REFERENCE_INVALID",
            "use a YouTube channel URL, @handle, or channel ID",
        )
    path = parsed.path.strip("/")
    if path.startswith("channel/"):
        channel_id = path.split("/", 1)[1].split("/", 1)[0]
        if channel_id:
            return "id", channel_id
    if path.startswith("@"):
        return "handle", path[1:].split("/", 1)[0]
    if clean.startswith("@"):
        return "handle", clean[1:]
    if clean.startswith("UC"):
        return "id", clean
    raise ProductionError(
        "YOUTUBE_CHANNEL_REFERENCE_INVALID",
        "use a YouTube channel URL, @handle, or channel ID",
    )


async def resolve_youtube_channel(
    value: str,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> YouTubeChannel:
    """Validate a public channel via YouTube's official API, never a web scrape."""
    settings = settings or get_settings()
    if not settings.youtube_api_key:
        raise ProductionError("YOUTUBE_NOT_CONFIGURED", "YouTube discovery requires YOUTUBE_API_KEY")
    kind, reference = youtube_channel_reference(value)
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=15)
    try:
        params: dict[str, str] = {"key": settings.youtube_api_key, "part": "snippet,statistics"}
        params["id" if kind == "id" else "forHandle"] = reference
        response = await client.get("https://www.googleapis.com/youtube/v3/channels", params=params)
        response.raise_for_status()
        items = response.json().get("items", [])
        if not items:
            raise ProductionError("YOUTUBE_CHANNEL_NOT_FOUND", "the public YouTube channel was not found")
        item = items[0]
        snippet = item.get("snippet", {})
        thumbnails = snippet.get("thumbnails", {})
        channel_id = str(item.get("id") or "")
        if not channel_id:
            raise ProductionError("YOUTUBE_CHANNEL_NOT_FOUND", "the public YouTube channel was not found")
        recent = await search_youtube("", max_results=1, channel_id=channel_id, settings=settings, client=client)
        statistics = item.get("statistics", {})
        raw_count = statistics.get("videoCount")
        return YouTubeChannel(
            channel_id=channel_id,
            title=str(snippet.get("title") or "YouTube channel")[:500],
            url=f"https://www.youtube.com/channel/{channel_id}",
            thumbnail_url=(thumbnails.get("medium") or thumbnails.get("default") or {}).get("url"),
            video_count=int(raw_count) if raw_count is not None else None,
            latest_upload_title=recent[0].title if recent else None,
        )
    except httpx.HTTPError as error:
        raise ProductionError("YOUTUBE_DISCOVERY_FAILED", "YouTube discovery request failed") from error
    finally:
        if owns_client:
            await client.aclose()


async def search_youtube(
    query: str,
    max_results: int | None = None,
    channel_id: str | None = None,
    published_after: datetime | None = None,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[YouTubeVideo]:
    settings = settings or get_settings()
    if not settings.youtube_api_key:
        raise ProductionError(
            "YOUTUBE_NOT_CONFIGURED", "YouTube discovery requires YOUTUBE_API_KEY"
        )
    params: dict[str, str | int] = {
        "key": settings.youtube_api_key,
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": min(
            max_results or settings.youtube_search_max_results, settings.youtube_search_max_results
        ),
        "order": settings.youtube_search_default_order,
        "eventType": "none",
    }
    if channel_id:
        params["channelId"] = channel_id
    if published_after:
        params["publishedAfter"] = published_after.isoformat().replace("+00:00", "Z")
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=15)
    try:
        response = await client.get("https://www.googleapis.com/youtube/v3/search", params=params)
        response.raise_for_status()
        ids = [
            item["id"].get("videoId")
            for item in response.json().get("items", [])
            if item.get("id", {}).get("videoId")
        ]
        if not ids:
            return []
        detail = await client.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={
                "key": settings.youtube_api_key,
                "part": "snippet,contentDetails,statistics,liveStreamingDetails",
                "id": ",".join(ids),
            },
        )
        detail.raise_for_status()
        results: list[YouTubeVideo] = []
        for item in detail.json().get("items", []):
            snippet = item.get("snippet", {})
            thumbnails = snippet.get("thumbnails", {})
            published = snippet.get("publishedAt")
            results.append(
                YouTubeVideo(
                    item["id"],
                    snippet.get("title", "Untitled")[:500],
                    snippet.get("channelTitle", "")[:500],
                    (thumbnails.get("medium") or thumbnails.get("default") or {}).get("url"),
                    datetime.fromisoformat(published.replace("Z", "+00:00")) if published else None,
                    item.get("contentDetails", {}).get("duration"),
                    int(item.get("statistics", {}).get("viewCount", 0))
                    if item.get("statistics", {}).get("viewCount")
                    else None,
                )
            )
        return results
    except httpx.HTTPError as error:
        raise ProductionError(
            "YOUTUBE_DISCOVERY_FAILED", "YouTube discovery request failed"
        ) from error
    finally:
        if owns_client:
            await client.aclose()
