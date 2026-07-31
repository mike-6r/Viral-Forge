"""Lawful public-discovery provider boundaries; no login scraping or account automation."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from app.common.config import Settings, get_settings
from app.ingestion.feeds import parse_feed
from app.ingestion.http import SafeOutboundHttpClient
from app.production.youtube import search_youtube


class ProviderError(Exception):
    def __init__(self, category: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.category, self.retryable = category, retryable


@dataclass(frozen=True)
class ProviderCapabilities:
    supports_poll: bool
    supports_search: bool
    supports_cursor: bool
    requires_credentials: bool
    enabled: bool


@dataclass(frozen=True)
class ProviderMedia:
    item_id: str
    canonical_url: str
    title: str | None = None
    description: str | None = None
    uploader: str | None = None
    uploader_id: str | None = None
    published_at: datetime | None = None
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    thumbnail_url: str | None = None
    view_count: int | None = None
    language: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderPollResult:
    items: list[ProviderMedia]
    cursor: str | None = None
    rate_limit_remaining: int | None = None


class DiscoveryProvider(Protocol):
    name: str

    def capabilities(self) -> ProviderCapabilities: ...
    def validate(self, configuration: dict[str, object]) -> None: ...
    def poll(
        self, configuration: dict[str, object], cursor: str | None = None
    ) -> ProviderPollResult: ...


class DisabledPlatformProvider:
    def __init__(self, name: str, requirement: str) -> None:
        self.name, self.requirement = name, requirement

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(False, False, False, True, False)

    def validate(self, configuration: dict[str, object]) -> None:
        raise ProviderError("PROVIDER_DISABLED", self.requirement)

    def poll(
        self, configuration: dict[str, object], cursor: str | None = None
    ) -> ProviderPollResult:
        raise ProviderError("PROVIDER_DISABLED", self.requirement)


class YouTubeDiscoveryProvider:
    name = "YOUTUBE"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(True, True, True, True, bool(self.settings.youtube_api_key))

    def validate(self, configuration: dict[str, object]) -> None:
        if not self.settings.youtube_api_key:
            raise ProviderError("YOUTUBE_NOT_CONFIGURED", "YouTube Data API key is required")
        if not configuration.get("channel_id") and not configuration.get("keywords"):
            raise ProviderError("INVALID_CONFIGURATION", "channel_id or keywords is required")

    def poll(
        self, configuration: dict[str, object], cursor: str | None = None
    ) -> ProviderPollResult:
        self.validate(configuration)
        keywords = configuration.get("keywords")
        query = (
            " ".join(str(value) for value in keywords)
            if isinstance(keywords, list)
            else str(keywords or "")
        )
        configured_limit = configuration.get("result_limit")
        result_limit = (
            int(configured_limit)
            if isinstance(configured_limit, int | float | str)
            else self.settings.discovery_result_limit
        )
        results = asyncio.run(
            search_youtube(
                query or str(configuration.get("channel_id")),
                max_results=min(result_limit, self.settings.discovery_result_limit),
                channel_id=str(configuration["channel_id"])
                if configuration.get("channel_id")
                else None,
                settings=self.settings,
            )
        )
        return ProviderPollResult(
            [
                ProviderMedia(
                    item_id=item.video_id,
                    canonical_url=item.url,
                    title=item.title,
                    uploader=item.channel_title,
                    published_at=item.published_at,
                    thumbnail_url=item.thumbnail_url,
                    view_count=item.view_count,
                    metadata={"youtube_video_id": item.video_id},
                )
                for item in results
            ]
        )


class RssAtomDiscoveryProvider:
    name = "RSS"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(True, False, False, False, True)

    def validate(self, configuration: dict[str, object]) -> None:
        if not isinstance(configuration.get("feed_url"), str):
            raise ProviderError("INVALID_CONFIGURATION", "feed_url is required")

    def poll(
        self, configuration: dict[str, object], cursor: str | None = None
    ) -> ProviderPollResult:
        self.validate(configuration)
        url = str(configuration["feed_url"])
        try:
            result = asyncio.run(
                SafeOutboundHttpClient().fetch(
                    url,
                    accepted_content_types={
                        "application/rss+xml",
                        "application/atom+xml",
                        "application/xml",
                        "text/xml",
                    },
                )
            )
        except Exception as error:
            raise ProviderError(
                "RSS_FETCH_FAILED", "configured public feed could not be fetched", True
            ) from error
        _, _, entries = parse_feed(result.body)
        items: list[ProviderMedia] = []
        configured_limit = configuration.get("result_limit")
        result_limit = (
            int(configured_limit) if isinstance(configured_limit, int | float | str) else 20
        )
        for entry in entries[:result_limit]:
            link = str(entry.get("link") or "")
            published = entry.get("published_at")
            if link:
                items.append(
                    ProviderMedia(
                        str(entry.get("id") or hashlib.sha256(link.encode()).hexdigest()),
                        link,
                        str(entry.get("title") or "") or None,
                        str(entry.get("summary") or "") or None,
                        str(entry.get("author") or "") or None,
                        published_at=published if isinstance(published, datetime) else None,
                    )
                )
        return ProviderPollResult(items)


def default_providers(settings: Settings | None = None) -> dict[str, DiscoveryProvider]:
    return {
        "YOUTUBE": YouTubeDiscoveryProvider(settings),
        "RSS": RssAtomDiscoveryProvider(),
        "ATOM": RssAtomDiscoveryProvider(),
        "WEBPAGE": DisabledPlatformProvider(
            "WEBPAGE",
            "Generic webpage discovery must be implemented with an explicitly configured safe public parser.",
        ),
        "TIKTOK": DisabledPlatformProvider(
            "TIKTOK", "TikTok requires a supported official API or configured lawful integration."
        ),
        "INSTAGRAM": DisabledPlatformProvider(
            "INSTAGRAM", "Instagram requires a supported Meta API integration."
        ),
        "FACEBOOK": DisabledPlatformProvider(
            "FACEBOOK", "Facebook requires a supported Meta API integration."
        ),
        "X": DisabledPlatformProvider("X", "X requires an authorized API integration."),
    }
