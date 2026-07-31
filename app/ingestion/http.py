"""The single network boundary for ingestion metadata retrieval.

The client deliberately does not follow redirects itself: every destination is
normalised, DNS-checked, and policy-checked before it is requested.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urljoin, urlsplit

import httpx

from app.common.config import Settings, get_settings
from app.common.errors import DomainError
from app.ingestion.url import UrlSafetyError, normalize_url


class FetchErrorCategory(StrEnum):
    DNS_FAILURE = "DNS_FAILURE"
    CONNECTION_TIMEOUT = "CONNECTION_TIMEOUT"
    READ_TIMEOUT = "READ_TIMEOUT"
    TLS_FAILURE = "TLS_FAILURE"
    RATE_LIMITED = "RATE_LIMITED"
    REMOTE_SERVER_ERROR = "REMOTE_SERVER_ERROR"
    INVALID_URL = "INVALID_URL"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    SOURCE_NOT_ALLOWED = "SOURCE_NOT_ALLOWED"
    SSRF_BLOCKED = "SSRF_BLOCKED"
    TOO_MANY_REDIRECTS = "TOO_MANY_REDIRECTS"
    REDIRECT_LOOP = "REDIRECT_LOOP"
    UNSUPPORTED_CONTENT_TYPE = "UNSUPPORTED_CONTENT_TYPE"
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    REMOTE_CLIENT_ERROR = "REMOTE_CLIENT_ERROR"


RETRYABLE_CATEGORIES = {
    FetchErrorCategory.DNS_FAILURE,
    FetchErrorCategory.CONNECTION_TIMEOUT,
    FetchErrorCategory.READ_TIMEOUT,
    FetchErrorCategory.TLS_FAILURE,
    FetchErrorCategory.RATE_LIMITED,
    FetchErrorCategory.REMOTE_SERVER_ERROR,
}
ACCEPTED_CONTENT_TYPES = {"text/html", "application/xhtml+xml"}
BLOCKED_HOSTS = {
    "localhost",
    "metadata.google.internal",
    "metadata.aws.internal",
    "metadata.azure.internal",
    "instance-data",
}


class SafeFetchError(DomainError):
    def __init__(self, category: FetchErrorCategory, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.retryable = category in RETRYABLE_CATEGORIES


@dataclass(frozen=True)
class FetchResult:
    submitted_url: str
    final_url: str
    status_code: int
    content_type: str
    body: bytes
    redirects: tuple[str, ...]
    headers: dict[str, str]


Resolver = Callable[[str], Awaitable[list[str]]]
RedirectPolicy = Callable[[str], None]


async def resolve_hostname(hostname: str) -> list[str]:
    try:
        results = await asyncio.to_thread(socket.getaddrinfo, hostname, None, socket.AF_UNSPEC)
    except socket.gaierror as error:
        raise SafeFetchError(
            FetchErrorCategory.DNS_FAILURE, "destination DNS resolution failed"
        ) from error
    addresses = sorted({str(entry[4][0]) for entry in results})
    if not addresses:
        raise SafeFetchError(
            FetchErrorCategory.DNS_FAILURE, "destination DNS resolution returned no addresses"
        )
    return addresses


def validate_destination_host(hostname: str, addresses: list[str]) -> None:
    host = hostname.lower().rstrip(".")
    if host in BLOCKED_HOSTS or host.endswith((".local", ".internal", ".localhost")):
        raise SafeFetchError(
            FetchErrorCategory.SSRF_BLOCKED, "destination hostname is not permitted"
        )
    if not addresses:
        raise SafeFetchError(
            FetchErrorCategory.DNS_FAILURE, "destination DNS resolution returned no addresses"
        )
    for address in addresses:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as error:
            raise SafeFetchError(
                FetchErrorCategory.DNS_FAILURE, "destination DNS response was invalid"
            ) from error
        # is_global excludes loopback, private, link-local, multicast,
        # unspecified, documentation, shared/CGNAT, and other reserved ranges.
        if not parsed.is_global:
            raise SafeFetchError(
                FetchErrorCategory.SSRF_BLOCKED, "destination address is not publicly routable"
            )


class SafeOutboundHttpClient:
    """Fetch bounded HTML metadata without cookies, auth, proxies, or implicit redirects."""

    def __init__(
        self,
        settings: Settings | None = None,
        resolver: Resolver = resolve_hostname,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._resolver = resolver
        self._transport = transport

    async def _validate_url(self, url: str) -> str:
        try:
            normalized = normalize_url(url)
        except UrlSafetyError as error:
            category = (
                FetchErrorCategory.SSRF_BLOCKED
                if "network URL is blocked" in str(error)
                else FetchErrorCategory.INVALID_URL
            )
            raise SafeFetchError(category, "submitted URL is not permitted") from error
        host = urlsplit(normalized).hostname
        if host is None:
            raise SafeFetchError(FetchErrorCategory.INVALID_URL, "submitted URL is not permitted")
        addresses = await self._resolver(host)
        validate_destination_host(host, addresses)
        return normalized

    async def fetch(
        self,
        submitted_url: str,
        correlation_id: str | None = None,
        redirect_policy: RedirectPolicy | None = None,
        total_timeout_seconds: int | None = None,
        max_redirects: int | None = None,
        accepted_content_types: set[str] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> FetchResult:
        del correlation_id  # Kept in the boundary contract; it is not sent to untrusted hosts.
        current_url = await self._validate_url(submitted_url)
        redirects: list[str] = []
        deadline = total_timeout_seconds or self.settings.ingestion_http_total_timeout_seconds
        redirect_limit = (
            max_redirects
            if max_redirects is not None
            else self.settings.ingestion_http_max_redirects
        )
        timeout = httpx.Timeout(
            connect=self.settings.ingestion_http_connect_timeout_seconds,
            read=self.settings.ingestion_http_read_timeout_seconds,
            write=self.settings.ingestion_http_write_timeout_seconds,
            pool=self.settings.ingestion_http_pool_timeout_seconds,
        )
        headers = {
            "Accept": "text/html, application/xhtml+xml",
            "User-Agent": self.settings.ingestion_http_user_agent,
        }
        if extra_headers is not None:
            headers.update(
                {
                    key: value
                    for key, value in extra_headers.items()
                    if key in {"If-None-Match", "If-Modified-Since"}
                }
            )
        accepted_types = accepted_content_types or ACCEPTED_CONTENT_TYPES
        try:
            async with asyncio.timeout(deadline):
                async with httpx.AsyncClient(
                    follow_redirects=False,
                    headers=headers,
                    timeout=timeout,
                    trust_env=False,
                    verify=True,
                    transport=self._transport,
                ) as client:
                    while True:
                        request = client.build_request("GET", current_url)
                        response = await client.send(request, stream=True)
                        try:
                            if response.status_code in {301, 302, 303, 307, 308}:
                                location = response.headers.get("location")
                                if not location:
                                    raise SafeFetchError(
                                        FetchErrorCategory.INVALID_URL, "redirect had no location"
                                    )
                                if len(redirects) >= redirect_limit:
                                    raise SafeFetchError(
                                        FetchErrorCategory.TOO_MANY_REDIRECTS,
                                        "redirect limit exceeded",
                                    )
                                destination = await self._validate_url(
                                    urljoin(current_url, location)
                                )
                                if destination in redirects or destination == current_url:
                                    raise SafeFetchError(
                                        FetchErrorCategory.REDIRECT_LOOP, "redirect loop detected"
                                    )
                                if redirect_policy is not None:
                                    redirect_policy(destination)
                                redirects.append(current_url)
                                current_url = destination
                                continue
                            if response.status_code == 429:
                                raise SafeFetchError(
                                    FetchErrorCategory.RATE_LIMITED,
                                    "remote service rate limited the request",
                                )
                            if response.status_code >= 500:
                                raise SafeFetchError(
                                    FetchErrorCategory.REMOTE_SERVER_ERROR, "remote service failed"
                                )
                            if response.status_code >= 400:
                                raise SafeFetchError(
                                    FetchErrorCategory.REMOTE_CLIENT_ERROR,
                                    "remote resource was unavailable",
                                )
                            if response.status_code == 304:
                                return FetchResult(
                                    submitted_url,
                                    current_url,
                                    304,
                                    "",
                                    b"",
                                    tuple(redirects),
                                    dict(response.headers),
                                )
                            content_type = (
                                response.headers.get("content-type", "")
                                .split(";", 1)[0]
                                .lower()
                                .strip()
                            )
                            if content_type not in accepted_types:
                                raise SafeFetchError(
                                    FetchErrorCategory.UNSUPPORTED_CONTENT_TYPE,
                                    "response is not HTML metadata",
                                )
                            content_length = response.headers.get("content-length")
                            if (
                                content_length is not None
                                and content_length.isdigit()
                                and int(content_length)
                                > self.settings.ingestion_http_max_response_bytes
                            ):
                                raise SafeFetchError(
                                    FetchErrorCategory.RESPONSE_TOO_LARGE,
                                    "metadata response exceeds the configured limit",
                                )
                            chunks: list[bytes] = []
                            size = 0
                            async for chunk in response.aiter_bytes():
                                size += len(chunk)
                                if size > self.settings.ingestion_http_max_response_bytes:
                                    raise SafeFetchError(
                                        FetchErrorCategory.RESPONSE_TOO_LARGE,
                                        "metadata response exceeds the configured limit",
                                    )
                                chunks.append(chunk)
                            return FetchResult(
                                submitted_url=submitted_url,
                                final_url=current_url,
                                status_code=response.status_code,
                                content_type=content_type,
                                body=b"".join(chunks),
                                redirects=tuple(redirects),
                                headers=dict(response.headers),
                            )
                        finally:
                            await response.aclose()
        except SafeFetchError:
            raise
        except httpx.ConnectTimeout as error:
            raise SafeFetchError(
                FetchErrorCategory.CONNECTION_TIMEOUT, "connection timed out"
            ) from error
        except httpx.ReadTimeout as error:
            raise SafeFetchError(
                FetchErrorCategory.READ_TIMEOUT, "remote response timed out"
            ) from error
        except (httpx.WriteTimeout, httpx.PoolTimeout) as error:
            raise SafeFetchError(
                FetchErrorCategory.CONNECTION_TIMEOUT, "network operation timed out"
            ) from error
        except httpx.ConnectError as error:
            raise SafeFetchError(
                FetchErrorCategory.TLS_FAILURE, "secure connection could not be established"
            ) from error
        except httpx.HTTPError as error:
            raise SafeFetchError(
                FetchErrorCategory.REMOTE_SERVER_ERROR, "remote response could not be processed"
            ) from error
        except TimeoutError as error:
            raise SafeFetchError(
                FetchErrorCategory.READ_TIMEOUT, "metadata retrieval exceeded its deadline"
            ) from error
