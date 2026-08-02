"""Official TikTok Content Posting API boundary.

No browser automation, cookies, unofficial endpoints, or raw credential
persistence is permitted here.  The provider is deliberately small and uses
only Login Kit and Content Posting API v2 endpoints.
"""

import base64
import hashlib
import hmac
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.brands.models import DestinationAccount
from app.common.config import Settings, get_settings
from app.publishing.credentials import CredentialStore, credential_store
from app.publishing.models import TikTokCreatorCapability, TikTokOAuthState
from app.publishing.service import MediaValidation, PublishingError

TIKTOK_API_BASE = "https://open.tiktokapis.com"
TIKTOK_AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
TIKTOK_REVOKE_URL = "https://open.tiktokapis.com/v2/oauth/revoke/"


class TikTokMode:
    DRAFT_UPLOAD = "DRAFT_UPLOAD"
    DIRECT_POST = "DIRECT_POST"


@dataclass(frozen=True)
class TikTokTokenSet:
    access_token: str
    refresh_token: str | None
    open_id: str | None
    expires_in: int | None
    scopes: frozenset[str]

    def payload(self) -> dict[str, object]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token or "",
            "open_id": self.open_id or "",
            "expires_in": self.expires_in or 0,
            "expires_at": (
                (datetime.now(UTC) + timedelta(seconds=self.expires_in)).isoformat()
                if self.expires_in
                else ""
            ),
            "scope": ",".join(sorted(self.scopes)),
        }


@dataclass(frozen=True)
class TikTokInitialization:
    publish_id: str
    upload_url: str


@dataclass(frozen=True)
class CreatorCapabilities:
    creator_identity_reference: str
    username: str | None
    nickname: str | None
    privacy_options: list[str]
    max_video_duration_seconds: int | None
    comments_disabled: bool
    duet_disabled: bool
    stitch_disabled: bool
    provider_log_id: str | None


def transfer_chunk_sizes(total_bytes: int, configured_chunk_size: int) -> list[int]:
    """Return TikTok-valid sequential FILE_UPLOAD chunk sizes.

    TikTok allows a small video as one chunk.  For larger videos, a trailing
    remainder below 5 MB must be merged into the final regular chunk rather
    than sent as an invalid tiny request.
    """
    minimum = 5_000_000
    if total_bytes <= 0:
        raise PublishingError("TIKTOK_MEDIA_EMPTY", "rendered clip has no transferable bytes")
    chunk_size = min(configured_chunk_size, total_bytes)
    if total_bytes <= chunk_size:
        return [total_bytes]
    full_chunks, remainder = divmod(total_bytes, chunk_size)
    if not remainder:
        return [chunk_size] * full_chunks
    if remainder < minimum:
        return [chunk_size] * (full_chunks - 1) + [chunk_size + remainder]
    return [chunk_size] * full_chunks + [remainder]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _response_data(response: httpx.Response) -> tuple[dict[str, object], str | None]:
    try:
        body = cast(dict[str, Any], response.json())
    except json.JSONDecodeError as error:
        raise PublishingError("TIKTOK_PROVIDER_FAILED", "TikTok returned an invalid response") from error
    error_payload = body.get("error") or {}
    error_values = error_payload if isinstance(error_payload, dict) else {}
    code = str(error_values.get("code") or "")
    if response.status_code >= 400 or (code and code != "ok"):
        if response.status_code == 429 or code == "rate_limit_exceeded":
            raise PublishingError("TIKTOK_RATE_LIMIT", "TikTok rate limited this request")
        if response.status_code in {401, 403} or code in {"access_token_invalid", "scope_not_authorized"}:
            raise PublishingError("TIKTOK_AUTH_FAILED", "TikTok rejected the account credential or required scope")
        raise PublishingError("TIKTOK_PROVIDER_FAILED", "TikTok did not accept the request")
    data = body.get("data") or {}
    return data if isinstance(data, dict) else {}, str(error_values.get("log_id") or "") or None


class TikTokPublishingProvider:
    """TikTok's official API adapter, injectable through ``httpx`` in tests."""

    provider_name = "TIKTOK"

    def __init__(self, settings: Settings | None = None, store: CredentialStore | None = None) -> None:
        self.settings = settings or get_settings()
        self.store = store or credential_store(self.settings)

    def _tokens(self, account: DestinationAccount) -> TikTokTokenSet:
        if not account.credential_reference_id:
            raise PublishingError("TIKTOK_CREDENTIAL_UNAVAILABLE", "TikTok credential reference is unavailable")
        payload = self.store.read(account.credential_reference_id)
        access = str(payload.get("access_token") or "")
        if not access:
            raise PublishingError("TIKTOK_CREDENTIAL_UNAVAILABLE", "TikTok access token is unavailable from the external credential reference")
        scopes = payload.get("scope") or payload.get("scopes") or ""
        values = set(scopes.split(",")) if isinstance(scopes, str) else set(scopes if isinstance(scopes, list) else [])
        expires_raw = str(payload.get("expires_in") or "")
        return TikTokTokenSet(
            access,
            str(payload.get("refresh_token") or "") or None,
            str(payload.get("open_id") or "") or None,
            int(expires_raw) if expires_raw.isdigit() else None,
            frozenset(str(item).strip() for item in values if str(item).strip()),
        )

    def authorization_url(self, state: str, scopes: list[str], code_challenge: str | None = None) -> str:
        if not self.settings.tiktok_client_id:
            raise PublishingError("TIKTOK_NOT_CONFIGURED", "TikTok client key is not configured")
        params = {
            "client_key": self.settings.tiktok_client_id,
            "response_type": "code",
            "scope": ",".join(scopes),
            "redirect_uri": self.settings.oauth_callback_url("tiktok"),
            "state": state,
        }
        if code_challenge:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = "S256"
        return f"{TIKTOK_AUTH_URL}?{urlencode(params)}"

    def verify_connection(self, account: DestinationAccount) -> tuple[str | None, str | None]:
        """Verify the credential with TikTok's official Display API."""
        token = self._tokens(account)
        try:
            response = httpx.get(
                f"{TIKTOK_API_BASE}/v2/user/info/",
                params={"fields": "open_id,display_name"},
                headers={"Authorization": f"Bearer {token.access_token}"},
                timeout=self.settings.tiktok_request_timeout_seconds,
            )
            data, _ = _response_data(response)
        except httpx.HTTPError as error:
            raise PublishingError("TIKTOK_NETWORK_FAILURE", "could not reach TikTok to verify the connection") from error
        user = data.get("user")
        user_values = user if isinstance(user, dict) else {}
        identity = str(user_values.get("open_id") or token.open_id or "")
        if not identity:
            raise PublishingError("TIKTOK_CREATOR_IDENTITY_MISSING", "TikTok did not return the connected creator identity")
        return identity, None

    def exchange_code(self, code: str, code_verifier: str | None = None) -> TikTokTokenSet:
        from app.publishing.service import EnvironmentCredentialResolver

        secret = EnvironmentCredentialResolver().resolve(self.settings.tiktok_client_secret_credential_reference)
        payload: dict[str, str] = {"client_key": self.settings.tiktok_client_id or "", "client_secret": secret, "code": code, "grant_type": "authorization_code", "redirect_uri": self.settings.oauth_callback_url("tiktok")}
        if code_verifier:
            payload["code_verifier"] = code_verifier
        try:
            response = httpx.post(TIKTOK_TOKEN_URL, data=payload, timeout=self.settings.tiktok_request_timeout_seconds)
            data, _ = _response_data(response)
        except httpx.HTTPError as error:
            raise PublishingError("TIKTOK_NETWORK_FAILURE", "could not reach TikTok for token exchange") from error
        return TikTokTokenSet(str(data.get("access_token") or ""), str(data.get("refresh_token") or "") or None, str(data.get("open_id") or "") or None, int(str(data["expires_in"])) if str(data.get("expires_in") or "").isdigit() else None, frozenset(str(data.get("scope") or "").split(",")))

    def refresh_token(self, account: DestinationAccount) -> TikTokTokenSet:
        tokens = self._tokens(account)
        if not tokens.refresh_token:
            raise PublishingError("TIKTOK_REFRESH_UNAVAILABLE", "the externally managed TikTok credential has no refresh token")
        from app.publishing.service import EnvironmentCredentialResolver

        secret = EnvironmentCredentialResolver().resolve(self.settings.tiktok_client_secret_credential_reference)
        try:
            response = httpx.post(TIKTOK_TOKEN_URL, data={"client_key": self.settings.tiktok_client_id, "client_secret": secret, "grant_type": "refresh_token", "refresh_token": tokens.refresh_token}, timeout=self.settings.tiktok_request_timeout_seconds)
            data, _ = _response_data(response)
        except httpx.HTTPError as error:
            raise PublishingError("TIKTOK_NETWORK_FAILURE", "could not reach TikTok to refresh the credential") from error
        # Returned tokens intentionally leave this process through a vault adapter
        # integration; this provider never writes raw tokens to the database.
        return TikTokTokenSet(str(data.get("access_token") or ""), str(data.get("refresh_token") or "") or None, str(data.get("open_id") or "") or None, int(str(data["expires_in"])) if str(data.get("expires_in") or "").isdigit() else None, frozenset(str(data.get("scope") or "").split(",")))

    def revoke(self, account: DestinationAccount) -> None:
        token = self._tokens(account).access_token
        try:
            response = httpx.post(TIKTOK_REVOKE_URL, data={"client_key": self.settings.tiktok_client_id, "token": token}, timeout=self.settings.tiktok_request_timeout_seconds)
            _response_data(response)
        except httpx.HTTPError as error:
            raise PublishingError("TIKTOK_NETWORK_FAILURE", "could not reach TikTok to revoke the credential") from error

    def creator_info(self, account: DestinationAccount) -> CreatorCapabilities:
        token = self._tokens(account)
        if "video.publish" not in token.scopes:
            raise PublishingError("TIKTOK_VIDEO_PUBLISH_SCOPE_REQUIRED", "TikTok Direct Post requires the granted video.publish scope")
        try:
            response = httpx.post(f"{TIKTOK_API_BASE}/v2/post/publish/creator_info/query/", headers={"Authorization": f"Bearer {token.access_token}", "Content-Type": "application/json; charset=UTF-8"}, json={}, timeout=self.settings.tiktok_request_timeout_seconds)
            data, log_id = _response_data(response)
        except httpx.HTTPError as error:
            raise PublishingError("TIKTOK_NETWORK_FAILURE", "could not query TikTok creator capabilities") from error
        options = data.get("privacy_level_options")
        privacy_options = [str(value) for value in options] if isinstance(options, list) else []
        return CreatorCapabilities(str(data.get("creator_id") or token.open_id or ""), str(data.get("creator_username") or "") or None, str(data.get("creator_nickname") or "") or None, privacy_options, int(str(data["max_video_post_duration_sec"])) if str(data.get("max_video_post_duration_sec") or "").isdigit() else None, bool(data.get("comment_disabled", False)), bool(data.get("duet_disabled", False)), bool(data.get("stitch_disabled", False)), log_id)

    def initialize(self, account: DestinationAccount, mode: str, media: MediaValidation, metadata: dict[str, object], capability: CreatorCapabilities | None = None) -> TikTokInitialization:
        token = self._tokens(account)
        required_scope = "video.upload" if mode == TikTokMode.DRAFT_UPLOAD else "video.publish"
        if required_scope not in token.scopes:
            raise PublishingError("TIKTOK_SCOPE_REQUIRED", f"TikTok {mode} requires the granted {required_scope} scope")
        size = media.path.stat().st_size
        if size > self.settings.tiktok_max_media_bytes:
            raise PublishingError("TIKTOK_MEDIA_TOO_LARGE", "the rendered clip exceeds the configured TikTok media limit")
        chunk_sizes = transfer_chunk_sizes(size, self.settings.tiktok_transfer_chunk_size_bytes)
        chunk_size = min(self.settings.tiktok_transfer_chunk_size_bytes, size)
        chunks = len(chunk_sizes)
        source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk_size, "total_chunk_count": chunks}
        if mode == TikTokMode.DRAFT_UPLOAD:
            endpoint, body = "/v2/post/publish/inbox/video/init/", {"source_info": source}
        else:
            if capability is None:
                raise PublishingError("TIKTOK_CAPABILITIES_REQUIRED", "TikTok creator capabilities are required before Direct Post")
            privacy = str(metadata.get("privacy_level") or "SELF_ONLY")
            if privacy not in capability.privacy_options:
                raise PublishingError("TIKTOK_PRIVACY_INVALID", "selected TikTok privacy is unavailable for this creator")
            body = {"post_info": {"title": str(metadata.get("caption") or "")[:2200], "privacy_level": privacy, "disable_comment": bool(metadata.get("disable_comment", capability.comments_disabled)), "disable_duet": bool(metadata.get("disable_duet", capability.duet_disabled)), "disable_stitch": bool(metadata.get("disable_stitch", capability.stitch_disabled))}, "source_info": source}
            endpoint = "/v2/post/publish/video/init/"
        try:
            response = httpx.post(f"{TIKTOK_API_BASE}{endpoint}", headers={"Authorization": f"Bearer {token.access_token}", "Content-Type": "application/json; charset=UTF-8"}, json=body, timeout=self.settings.tiktok_request_timeout_seconds)
            data, _ = _response_data(response)
        except httpx.HTTPError as error:
            raise PublishingError("TIKTOK_NETWORK_FAILURE", "could not initialize the TikTok transfer") from error
        publish_id, upload_url = str(data.get("publish_id") or ""), str(data.get("upload_url") or "")
        if not publish_id or not upload_url:
            raise PublishingError("TIKTOK_INIT_FAILED", "TikTok did not return a transfer identifier")
        return TikTokInitialization(publish_id, upload_url)

    def transfer(self, initialization: TikTokInitialization, media: MediaValidation, progress: Callable[[int], None] | None = None) -> None:
        total = media.path.stat().st_size
        chunk_sizes = transfer_chunk_sizes(total, self.settings.tiktok_transfer_chunk_size_bytes)
        try:
            with media.path.open("rb") as handle:
                offset = 0
                for expected_size in chunk_sizes:
                    chunk = handle.read(expected_size)
                    if len(chunk) != expected_size:
                        raise PublishingError("TIKTOK_TRANSFER_FAILED", "rendered clip changed during transfer")
                    end = offset + len(chunk) - 1
                    response = httpx.put(initialization.upload_url, content=chunk, headers={"Content-Type": media.mime_type, "Content-Length": str(len(chunk)), "Content-Range": f"bytes {offset}-{end}/{total}"}, timeout=self.settings.tiktok_upload_timeout_seconds)
                    if response.status_code >= 400:
                        if end == total - 1 and response.status_code >= 500:
                            raise PublishingError(
                                "TIKTOK_UNKNOWN_REMOTE_OUTCOME",
                                "TikTok transfer completion is uncertain and requires reconciliation",
                            )
                        raise PublishingError("TIKTOK_TRANSFER_FAILED", "TikTok rejected a video transfer chunk")
                    offset += len(chunk)
                    if progress:
                        progress(min(99, int(offset * 100 / total)))
        except httpx.HTTPError as error:
            raise PublishingError(
                "TIKTOK_UNKNOWN_REMOTE_OUTCOME",
                "TikTok transfer outcome is uncertain and requires reconciliation",
            ) from error

    def status(self, account: DestinationAccount, publish_id: str) -> tuple[str, int | None, str | None, str | None]:
        token = self._tokens(account)
        try:
            response = httpx.post(f"{TIKTOK_API_BASE}/v2/post/publish/status/fetch/", headers={"Authorization": f"Bearer {token.access_token}", "Content-Type": "application/json; charset=UTF-8"}, json={"publish_id": publish_id}, timeout=self.settings.tiktok_request_timeout_seconds)
            data, _ = _response_data(response)
        except httpx.HTTPError as error:
            raise PublishingError("TIKTOK_NETWORK_FAILURE", "could not fetch TikTok post status") from error
        ids = data.get("publicaly_available_post_id") or data.get("publicly_available_post_id") or []
        post_id = str(ids[0]) if isinstance(ids, list) and ids else None
        uploaded = int(str(data["uploaded_bytes"])) if str(data.get("uploaded_bytes") or "").isdigit() else None
        return str(data.get("status") or "PROCESSING"), uploaded, post_id, str(data.get("fail_reason") or "") or None


def create_oauth_state(session: Session, account: DestinationAccount, settings: Settings | None = None, scopes: list[str] | None = None) -> tuple[TikTokOAuthState, str]:
    settings = settings or get_settings()
    settings.require_trusted_https_feature()
    if account.provider.upper() != "TIKTOK" or not account.is_active:
        raise PublishingError("TIKTOK_DESTINATION_INVALID", "an active TikTok destination is required")
    store = credential_store(settings)
    raw = secrets.token_urlsafe(48)
    verifier = secrets.token_urlsafe(64)
    secret = settings.tiktok_oauth_state_secret or ""
    digest = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    verifier_reference = store.create({"code_verifier": verifier}, namespace="oauth")
    state = TikTokOAuthState(brand_id=account.brand_id, destination_account_id=account.id, state_digest=digest, requested_scopes=scopes or ["user.info.basic", "video.upload"], pkce_verifier_reference=verifier_reference, expires_at=(datetime.now(UTC) + timedelta(seconds=settings.tiktok_oauth_state_ttl_seconds)).isoformat())
    session.add(state)
    session.commit()
    return state, raw


def oauth_code_challenge(session: Session, state: TikTokOAuthState, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    if not state.pkce_verifier_reference:
        raise PublishingError("TIKTOK_OAUTH_STATE_INVALID", "TikTok authorization state is invalid")
    verifier = credential_store(settings).read(state.pkce_verifier_reference).get("code_verifier")
    if not isinstance(verifier, str) or not verifier:
        raise PublishingError("TIKTOK_OAUTH_STATE_INVALID", "TikTok authorization state is invalid")
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def consume_oauth_verifier(state: TikTokOAuthState, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    if not state.pkce_verifier_reference:
        raise PublishingError("TIKTOK_OAUTH_STATE_INVALID", "TikTok authorization state is invalid")
    store = credential_store(settings)
    try:
        verifier = store.read(state.pkce_verifier_reference).get("code_verifier")
    finally:
        store.delete(state.pkce_verifier_reference)
    if not isinstance(verifier, str) or not verifier:
        raise PublishingError("TIKTOK_OAUTH_STATE_INVALID", "TikTok authorization state is invalid")
    return verifier


def consume_oauth_state(session: Session, raw_state: str, settings: Settings | None = None) -> TikTokOAuthState:
    settings = settings or get_settings()
    digest = hmac.new((settings.tiktok_oauth_state_secret or "").encode(), raw_state.encode(), hashlib.sha256).hexdigest()
    state = session.scalar(select(TikTokOAuthState).where(TikTokOAuthState.state_digest == digest))
    if state is None or state.consumed_at or datetime.fromisoformat(state.expires_at) <= datetime.now(UTC):
        raise PublishingError("TIKTOK_OAUTH_STATE_INVALID", "TikTok authorization state is invalid or expired")
    state.consumed_at = _now()
    session.commit()
    return state


def persist_capabilities(session: Session, account: DestinationAccount, capability: CreatorCapabilities) -> TikTokCreatorCapability:
    if not capability.creator_identity_reference:
        raise PublishingError("TIKTOK_CREATOR_IDENTITY_MISSING", "TikTok did not return a creator identity")
    record = session.scalar(select(TikTokCreatorCapability).where(TikTokCreatorCapability.destination_account_id == account.id))
    if record is None:
        record = TikTokCreatorCapability(destination_account_id=account.id, brand_id=account.brand_id, creator_identity_reference=capability.creator_identity_reference, captured_at=_now())
    record.creator_identity_reference = capability.creator_identity_reference
    record.creator_username, record.creator_nickname = capability.username, capability.nickname
    record.privacy_options, record.max_video_duration_seconds = capability.privacy_options, capability.max_video_duration_seconds
    record.comments_disabled, record.duet_disabled, record.stitch_disabled = capability.comments_disabled, capability.duet_disabled, capability.stitch_disabled
    record.provider_log_id, record.captured_at = capability.provider_log_id, _now()
    session.add(record)
    session.commit()
    return record
