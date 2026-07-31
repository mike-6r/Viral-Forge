from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.common.config import Settings
from app.discovery.models import DiscoveredMedia, DiscoverySource, DiscoveryStatus, DuplicateStatus
from app.discovery.providers import (
    ProviderCapabilities,
    ProviderError,
    ProviderMedia,
    ProviderPollResult,
)
from app.discovery.service import DiscoveryError, approve_media, reject_media, run_source
from tests.conftest import DEV_ACTOR_ID


class FakeProvider:
    name = "YOUTUBE"

    def __init__(self, items: list[ProviderMedia], error: ProviderError | None = None) -> None:
        self.items, self.error = items, error

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(True, True, True, True, True)

    def validate(self, configuration: dict[str, object]) -> None:
        return None

    def poll(
        self, configuration: dict[str, object], cursor: str | None = None
    ) -> ProviderPollResult:
        if self.error:
            raise self.error
        return ProviderPollResult(self.items, cursor="next")


def source(session, trusted: bool = False) -> DiscoverySource:  # type: ignore[no-untyped-def]
    record = DiscoverySource(
        name="Example PD YouTube",
        provider="YOUTUBE",
        source_type="CHANNEL",
        platform="YOUTUBE",
        agency_reference="Example PD",
        public_url="https://www.youtube.com/channel/example",
        trusted=trusted,
        configuration_json={
            "keywords": ["pursuit"],
            "excluded_keywords": ["community"],
            "categories": ["body camera"],
        },
    )
    session.add(record)
    session.commit()
    return record


def test_discovery_run_duplicate_relevance_and_approval_idempotency(session):  # type: ignore[no-untyped-def]
    settings = Settings(discovery_enabled=True, discovery_min_relevance_score=10)
    item = ProviderMedia(
        "video-1",
        "https://youtu.be/AbCdEf_1234",
        "Body camera pursuit",
        "Example PD pursuit",
        "Example PD",
        published_at=datetime.now(UTC),
    )
    record = source(session, trusted=True)
    first = run_source(session, DEV_ACTOR_ID, record, {"YOUTUBE": FakeProvider([item])}, settings)
    assert first.new_count == 1 and first.status == "SUCCEEDED"
    media = session.scalar(
        select(DiscoveredMedia).where(DiscoveredMedia.provider_item_id == "video-1")
    )
    assert media is not None and media.lifecycle_status == DiscoveryStatus.NEEDS_REVIEW
    assert media.duplicate_status == DuplicateStatus.NOT_DUPLICATE and media.discovery_score >= 10
    second = run_source(session, DEV_ACTOR_ID, record, {"YOUTUBE": FakeProvider([item])}, settings)
    assert second.duplicate_count == 1
    approved = approve_media(session, DEV_ACTOR_ID, media, media.review_version)
    assert (
        approved.production_project_id is not None
        and approved.lifecycle_status == DiscoveryStatus.PROJECT_CREATED
    )
    assert (
        approve_media(
            session, DEV_ACTOR_ID, approved, approved.review_version
        ).production_project_id
        == approved.production_project_id
    )


def test_discovery_failures_are_isolated_and_stale_rejection_is_blocked(session):  # type: ignore[no-untyped-def]
    settings = Settings(discovery_enabled=True)
    record = source(session)
    failed = run_source(
        session,
        DEV_ACTOR_ID,
        record,
        {"YOUTUBE": FakeProvider([], ProviderError("RATE_LIMIT", "quota exhausted", True))},
        settings,
    )
    assert (
        failed.status == "FAILED"
        and record.failure_count == 1
        and record.last_error_category == "RATE_LIMIT"
    )
    media = DiscoveredMedia(
        discovery_source_id=record.id,
        provider_item_id="two",
        canonical_url="https://youtu.be/ZyXwVu_1234",
        submitted_url="https://youtu.be/ZyXwVu_1234",
        platform="YOUTUBE",
        discovered_at=datetime.now(UTC),
        lifecycle_status=DiscoveryStatus.NEEDS_REVIEW,
    )
    session.add(media)
    session.commit()
    rejected = reject_media(session, DEV_ACTOR_ID, media, media.review_version, "Not relevant")
    assert rejected.lifecycle_status == DiscoveryStatus.REJECTED
    with pytest.raises(DiscoveryError, match="discovery item changed"):
        reject_media(session, DEV_ACTOR_ID, rejected, 1)
