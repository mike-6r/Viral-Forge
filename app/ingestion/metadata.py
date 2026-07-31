"""Bounded, non-executing extraction of page metadata from fetched HTML."""

import re
from dataclasses import dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.ingestion.url import UrlSafetyError, normalize_url

MAX_TITLE_LENGTH = 500
MAX_DESCRIPTION_LENGTH = 5_000
MAX_SHORT_VALUE_LENGTH = 500
MAX_URL_LENGTH = 2_048
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class ExtractedMetadata:
    selected: dict[str, str | None]
    raw: dict[str, str | None]
    warnings: tuple[str, ...]


def _clean(value: object | None, maximum: int) -> str | None:
    if value is None:
        return None
    cleaned = _CONTROL_CHARACTERS.sub(" ", str(value)).strip()
    if not cleaned:
        return None
    return cleaned[:maximum]


def _meta(soup: BeautifulSoup, *names: str) -> str | None:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if tag is not None:
            value = tag.get("content")
            if value:
                return str(value)
    return None


def _safe_metadata_url(value: str | None, base_url: str, warnings: list[str]) -> str | None:
    if value is None:
        return None
    try:
        return normalize_url(urljoin(base_url, value))
    except UrlSafetyError:
        warnings.append("untrusted metadata URL was discarded")
        return None


def extract_metadata(
    body: bytes, final_url: str, status_code: int, content_type: str
) -> ExtractedMetadata:
    """Parse one bounded response; this function does not perform network I/O."""
    text = body.decode("utf-8", errors="replace")
    soup = BeautifulSoup(text, "html.parser")
    warnings: list[str] = []
    canonical_tag = soup.find(
        "link", attrs={"rel": lambda value: bool(value and "canonical" in value)}
    )
    canonical_raw = canonical_tag.get("href") if canonical_tag is not None else None
    canonical = _safe_metadata_url(_clean(canonical_raw, MAX_URL_LENGTH), final_url, warnings)
    og_image = _safe_metadata_url(
        _clean(_meta(soup, "og:image"), MAX_URL_LENGTH), final_url, warnings
    )
    og_video = _safe_metadata_url(
        _clean(_meta(soup, "og:video"), MAX_URL_LENGTH), final_url, warnings
    )
    og_video_secure = _safe_metadata_url(
        _clean(_meta(soup, "og:video:secure_url"), MAX_URL_LENGTH), final_url, warnings
    )
    html_title = _clean(soup.title.string if soup.title is not None else None, MAX_TITLE_LENGTH)
    raw: dict[str, str | None] = {
        "html_title": html_title,
        "meta_description": _clean(_meta(soup, "description"), MAX_DESCRIPTION_LENGTH),
        "canonical_url": canonical,
        "og_title": _clean(_meta(soup, "og:title"), MAX_TITLE_LENGTH),
        "og_description": _clean(_meta(soup, "og:description"), MAX_DESCRIPTION_LENGTH),
        "og_type": _clean(_meta(soup, "og:type"), MAX_SHORT_VALUE_LENGTH),
        "og_image_url": og_image,
        "og_video_url": og_video,
        "og_video_secure_url": og_video_secure,
        "og_video_type": _clean(_meta(soup, "og:video:type"), MAX_SHORT_VALUE_LENGTH),
        "og_video_width": _clean(_meta(soup, "og:video:width"), MAX_SHORT_VALUE_LENGTH),
        "og_video_height": _clean(_meta(soup, "og:video:height"), MAX_SHORT_VALUE_LENGTH),
        "twitter_card": _clean(_meta(soup, "twitter:card"), MAX_SHORT_VALUE_LENGTH),
        "twitter_title": _clean(_meta(soup, "twitter:title"), MAX_TITLE_LENGTH),
        "twitter_description": _clean(_meta(soup, "twitter:description"), MAX_DESCRIPTION_LENGTH),
        "twitter_image_url": _safe_metadata_url(
            _clean(_meta(soup, "twitter:image"), MAX_URL_LENGTH), final_url, warnings
        ),
        "author": _clean(_meta(soup, "author", "article:author"), MAX_SHORT_VALUE_LENGTH),
        "site_name": _clean(
            _meta(soup, "og:site_name", "application-name"), MAX_SHORT_VALUE_LENGTH
        ),
        "published_at": _clean(
            _meta(soup, "article:published_time", "date"), MAX_SHORT_VALUE_LENGTH
        ),
        "modified_at": _clean(
            _meta(soup, "article:modified_time", "last-modified"), MAX_SHORT_VALUE_LENGTH
        ),
        "language": _clean(
            soup.html.get("lang") if soup.html is not None else None, MAX_SHORT_VALUE_LENGTH
        ),
        "final_url": final_url,
        "http_status": str(status_code),
        "response_content_type": content_type,
    }
    selected = {
        "title": raw["og_title"] or raw["twitter_title"] or raw["html_title"],
        "description": raw["og_description"]
        or raw["twitter_description"]
        or raw["meta_description"],
        "canonical_url": canonical,
        "final_url": final_url,
        "site_name": raw["site_name"],
        "author": raw["author"],
        "language": raw["language"],
    }
    return ExtractedMetadata(selected=selected, raw=raw, warnings=tuple(sorted(set(warnings))))
