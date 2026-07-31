"""Official YouTube Data API discovery boundary; no search-page scraping."""

from dataclasses import dataclass
from datetime import datetime

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
