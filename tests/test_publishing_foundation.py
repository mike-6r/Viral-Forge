import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.brands.models import Brand, DestinationAccount
from app.brands.service import ensure_legacy_brand
from app.content_packages.models import ContentPackage, ContentPackageStatus
from app.production.models import PostingQueueItem, ProductionClip, ProductionProject
from app.publishing.models import PublishRequest, PublishRequestStatus
from app.publishing.service import (
    PublishingError,
    YouTubeShortsOAuthProvider,
    cancel_publish,
    confirm_publish,
    request_publish,
    set_review_gate,
    verify_destination_connection,
)
from tests.conftest import DEV_ACTOR_ID


def _ready_publish_context(
    session: Session,
) -> tuple[Brand, ProductionClip, ContentPackage, DestinationAccount]:
    brand = ensure_legacy_brand(session)
    project = ProductionProject(
        brand_id=brand.id,
        source_url=f"https://www.youtube.com/watch?v={uuid.uuid4().hex[:11]}",
        status="CLIPS_READY",
        created_actor_id=DEV_ACTOR_ID,
    )
    session.add(project)
    session.flush()
    clip = ProductionClip(
        brand_id=brand.id,
        project_id=project.id,
        clip_number=1,
        start_seconds=0,
        end_seconds=20,
        duration_seconds=20,
        storage_key="assets/clip.mp4",
        render_status="SUCCEEDED",
        approval_status="APPROVED",
        publication_status="READY_TO_POST",
    )
    session.add(clip)
    session.flush()
    queue = PostingQueueItem(brand_id=brand.id, clip_id=clip.id, status="READY_TO_POST")
    package = ContentPackage(
        brand_id=brand.id,
        clip_id=clip.id,
        project_id=project.id,
        generation_version=1,
        status=ContentPackageStatus.APPROVED,
        provider_name="mock",
        explanation="Approved review package.",
        fields_json={"youtube_shorts_title": "Verified short", "description": "Source summary", "hashtags": ["verified"]},
    )
    destination = DestinationAccount(
        brand_id=brand.id,
        provider="YOUTUBE",
        account_reference="channel-reference",
        credential_reference_id="env://TEST_YOUTUBE_TOKEN",
        display_name="Test channel",
    )
    session.add_all([queue, package, destination])
    session.flush()
    session.add(AuditEvent(actor_id=DEV_ACTOR_ID, entity_type="production_project", entity_id=project.id, brand_id=brand.id, event_name="production.source.accepted"))
    session.commit()
    set_review_gate(session, DEV_ACTOR_ID, clip, False, "NOT_APPLICABLE", "APPROVED")
    return brand, clip, package, destination


def test_publish_requires_explicit_request_confirmation_and_can_cancel(session):  # type: ignore[no-untyped-def]
    _, clip, package, destination = _ready_publish_context(session)
    request = request_publish(session, DEV_ACTOR_ID, clip, package, destination, "publish-key-001", "MANUAL")
    assert request.status == PublishRequestStatus.AWAITING_CONFIRMATION
    assert request.platform_metadata["privacyStatus"] == "unlisted"
    assert request_publish(session, DEV_ACTOR_ID, clip, package, destination, "publish-key-001", "MANUAL").id == request.id
    confirmed = confirm_publish(session, DEV_ACTOR_ID, request)
    assert confirmed.status == PublishRequestStatus.QUEUED
    assert session.scalar(select(PublishRequest).where(PublishRequest.id == request.id)).remote_post_id is None
    cancelled = cancel_publish(session, DEV_ACTOR_ID, confirmed)
    assert cancelled.status == PublishRequestStatus.CANCELLED
    assert cancelled.cancelled_before_upload is True


def test_publish_rejects_missing_moderation_or_cross_brand_destination(session):  # type: ignore[no-untyped-def]
    brand, clip, package, destination = _ready_publish_context(session)
    set_review_gate(session, DEV_ACTOR_ID, clip, False, "NOT_APPLICABLE", "PENDING")
    with pytest.raises(PublishingError, match="moderation"):
        request_publish(session, DEV_ACTOR_ID, clip, package, destination, "publish-key-002", "MANUAL")
    set_review_gate(session, DEV_ACTOR_ID, clip, False, "NOT_APPLICABLE", "APPROVED")
    other = Brand(workspace_id=session.get(Brand, brand.id).workspace_id, name="Other", slug="other")
    session.add(other)
    session.flush()
    destination.brand_id = other.id
    session.commit()
    with pytest.raises(PublishingError, match="destination"):
        request_publish(session, DEV_ACTOR_ID, clip, package, destination, "publish-key-003", "MANUAL")


def test_connection_state_is_safe_and_public_youtube_upload_is_forbidden(session):  # type: ignore[no-untyped-def]
    _, _, _, destination = _ready_publish_context(session)
    connection = verify_destination_connection(session, DEV_ACTOR_ID, destination)
    assert connection.connection_state == "ERROR"
    assert connection.last_error_category == "CREDENTIAL_UNAVAILABLE"
    with pytest.raises(PublishingError, match="private or unlisted"):
        YouTubeShortsOAuthProvider().upload(destination, None, {"privacyStatus": "public"})  # type: ignore[arg-type]
