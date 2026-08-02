import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.brands.models import Brand, DestinationAccount
from app.common.config import Settings
from app.publishing.models import PublishingAccountConnection, PublishRequestStatus
from app.publishing.service import (
    MediaValidation,
    PublishingError,
    complete_tiktok_draft,
    execute_tiktok_publish,
    request_tiktok_publish,
)
from app.publishing.tiktok import (
    TikTokInitialization,
    TikTokMode,
    TikTokPublishingProvider,
    consume_oauth_state,
    create_oauth_state,
)
from tests.conftest import DEV_ACTOR_ID
from tests.test_publishing_foundation import _ready_publish_context


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "tiktok_enabled": True,
        "tiktok_client_id": "test-client",
        "tiktok_client_secret_credential_reference": "env://TEST_TIKTOK_CLIENT_SECRET",
        "tiktok_oauth_state_secret": "x" * 32,
        "tiktok_draft_upload_enabled": True,
        "tiktok_direct_post_enabled": True,
        "tiktok_emergency_pause": False,
        "tiktok_minimum_transfer_interval_seconds": 0,
    }
    values.update(overrides)
    return Settings(**values)


def _tiktok_destination(session: Session, brand_id: uuid.UUID) -> DestinationAccount:
    destination = DestinationAccount(
        brand_id=brand_id,
        provider="TIKTOK",
        account_reference="creator-open-id",
        credential_reference_id="env://TEST_TIKTOK_TOKEN_JSON",
        display_name="Test TikTok",
    )
    session.add(destination)
    session.flush()
    session.add(PublishingAccountConnection(destination_account_id=destination.id, connection_state="CONNECTED", provider_account_id="creator-open-id"))
    session.commit()
    return destination


def test_tiktok_request_is_brand_scoped_explicit_and_idempotent(session: Session) -> None:
    brand, clip, package, _ = _ready_publish_context(session)
    destination = _tiktok_destination(session, brand.id)
    settings = _settings()
    request = request_tiktok_publish(session, DEV_ACTOR_ID, clip, package, destination, "tiktok-draft-key-001", TikTokMode.DRAFT_UPLOAD, settings=settings)
    assert request.status == PublishRequestStatus.AWAITING_CONFIRMATION
    assert request.provider_mode == TikTokMode.DRAFT_UPLOAD
    assert request.platform_metadata["privacy_level"] == "SELF_ONLY"
    assert request_tiktok_publish(session, DEV_ACTOR_ID, clip, package, destination, "tiktok-draft-key-001", TikTokMode.DRAFT_UPLOAD, settings=settings).id == request.id
    other = Brand(workspace_id=brand.workspace_id, name="Other", slug=f"other-{uuid.uuid4().hex[:6]}")
    session.add(other)
    session.flush()
    destination.brand_id = other.id
    session.commit()
    with pytest.raises(PublishingError, match="owned by the clip brand"):
        request_tiktok_publish(session, DEV_ACTOR_ID, clip, package, destination, "tiktok-draft-key-002", TikTokMode.DRAFT_UPLOAD, settings=settings)


def test_unaudited_direct_post_forces_self_only_and_oauth_state_is_one_time(session: Session) -> None:
    brand, clip, package, _ = _ready_publish_context(session)
    destination = _tiktok_destination(session, brand.id)
    settings = _settings(tiktok_application_review_state="UNAUDITED")
    request = request_tiktok_publish(session, DEV_ACTOR_ID, clip, package, destination, "tiktok-direct-key-001", TikTokMode.DIRECT_POST, "SELF_ONLY", settings)
    assert request.platform_metadata["privacy_level"] == "SELF_ONLY"
    with pytest.raises(PublishingError, match="public"):
        request_tiktok_publish(session, DEV_ACTOR_ID, clip, package, destination, "tiktok-direct-key-002", TikTokMode.DIRECT_POST, "PUBLIC_TO_EVERYONE", settings)
    state, raw = create_oauth_state(session, destination, settings)
    assert state.state_digest != raw
    assert consume_oauth_state(session, raw, settings).id == state.id
    with pytest.raises(PublishingError, match="invalid or expired"):
        consume_oauth_state(session, raw, settings)


def test_draft_transfer_requires_operator_completion_without_marking_published(monkeypatch: pytest.MonkeyPatch, session: Session, tmp_path: Path) -> None:
    brand, clip, package, _ = _ready_publish_context(session)
    destination = _tiktok_destination(session, brand.id)
    settings = _settings()
    request = request_tiktok_publish(session, DEV_ACTOR_ID, clip, package, destination, "tiktok-transfer-key-001", TikTokMode.DRAFT_UPLOAD, settings=settings)
    request.status, request.confirmed_by_id = PublishRequestStatus.QUEUED, DEV_ACTOR_ID
    session.commit()
    media_path = tmp_path / "clip.mp4"
    media_path.write_bytes(b"x" * 1024)
    media = MediaValidation(media_path, "video/mp4", 20, "h264", "aac", 1080, 1920, 30)
    monkeypatch.setattr("app.publishing.service._media_validation", lambda *_: media)
    monkeypatch.setattr(TikTokPublishingProvider, "initialize", lambda *_: TikTokInitialization("publish-123", "https://upload.invalid/redacted"))
    monkeypatch.setattr(TikTokPublishingProvider, "transfer", lambda *_args, **_kwargs: None)
    result = execute_tiktok_publish(session, request.id, settings)
    assert result.status == PublishRequestStatus.OPERATOR_COMPLETION_REQUIRED
    assert result.remote_post_id == "publish-123"
    assert result.remote_post_url is None
    assert clip.publication_status != "PUBLISHED"
    completed = complete_tiktok_draft(session, DEV_ACTOR_ID, result, "POSTED", "https://www.tiktok.com/@creator/video/123")
    assert completed.status == PublishRequestStatus.SUCCEEDED
    assert completed.remote_post_url.endswith("/123")


def test_tiktok_provider_never_requires_real_network_for_unit_configuration(monkeypatch: pytest.MonkeyPatch, session: Session) -> None:
    brand, _, _, _ = _ready_publish_context(session)
    destination = _tiktok_destination(session, brand.id)
    monkeypatch.setenv("TEST_TIKTOK_TOKEN_JSON", json.dumps({"access_token": "not-logged", "scope": "video.upload,video.publish"}))
    provider = TikTokPublishingProvider(_settings())
    assert provider._tokens(destination).scopes == frozenset({"video.upload", "video.publish"})
