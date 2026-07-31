import ipaddress
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.common.errors import DomainError

TRACKING_PARAMETERS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
}


class UrlSafetyError(DomainError):
    pass


def normalize_url(submitted_url: str) -> str:
    try:
        parsed = urlsplit(submitted_url.strip())
    except ValueError as error:
        raise UrlSafetyError("invalid URL") from error
    if parsed.scheme.lower() not in {"http", "https"}:
        raise UrlSafetyError("only HTTP and HTTPS URLs are supported")
    if not parsed.hostname or parsed.username or parsed.password:
        raise UrlSafetyError("URL host is invalid or contains embedded credentials")
    host = parsed.hostname.encode("idna").decode("ascii").lower()
    try:
        address = ipaddress.ip_address(host)
        if not address.is_global:
            raise UrlSafetyError("private or local network URL is blocked")
    except ValueError:
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
            raise UrlSafetyError("local network URL is blocked") from None
    port = parsed.port
    authority = (
        host
        if port is None
        or (parsed.scheme.lower() == "http" and port == 80)
        or (parsed.scheme.lower() == "https" and port == 443)
        else f"{host}:{port}"
    )
    path = "/" + "/".join(part for part in parsed.path.split("/") if part)
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in TRACKING_PARAMETERS
        ),
        doseq=True,
    )
    return urlunsplit((parsed.scheme.lower(), authority, path, query, ""))
