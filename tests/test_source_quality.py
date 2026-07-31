import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.audit.models import AuditEvent
from app.common.config import Settings
from app.production.models import ProductionSource
from app.production.service import ProductionError, choose_source, create_project
from app.production.source_quality import (
    SourceMetadata,
    WatermarkDetectionService,
    WatermarkStatus,
    fingerprint_similarity,
    source_fingerprint,
)
from app.production.source_resolver import OriginalSourceResolver
from tests.conftest import DEV_ACTOR_ID


class FakeMetadataProvider:
    def __init__(self, source: SourceMetadata) -> None:
        self.source = source

    def extract(self, _: str) -> SourceMetadata:
        return self.source


class FakeSearchProvider:
    def __init__(self, candidates: list[SourceMetadata]) -> None:
        self.candidates = candidates

    def search(self, *_: object) -> list[SourceMetadata]:
        return self.candidates


def settings_with_registry(tmp_path: Path) -> Settings:
    registry = tmp_path / "official.yml"
    registry.write_text(
        "agencies:\n  - name: Example Police\n    aliases: [Example PD]\n    youtube_channels: [OFFICIAL]\n    enabled: true\n",
        encoding="utf-8",
    )
    return Settings(
        source_official_registry_path=str(registry),
        source_quality_weights_path="config/source_quality_weights.yml",
    )


def metadata(
    url: str, account: str, title: str, width: int = 1920, hint: str = ""
) -> SourceMetadata:
    return SourceMetadata(
        "YOUTUBE",
        url,
        uploader_name=account,
        uploader_account_id=account,
        video_title=title,
        description=hint,
        upload_date="2024-01-01",
        duration_seconds=80,
        width=width,
        height=1080,
        frame_rate=30,
        bitrate=3_000_000,
        metadata_json={"audio_present": True},
    )


def test_verified_official_beats_higher_resolution_repost_with_explanation(tmp_path: Path):
    settings = settings_with_registry(tmp_path)
    submitted = metadata(
        "https://youtu.be/AbCdEf_1234",
        "REPOST",
        "Incident clip repost",
        3840,
        "platform watermark repost",
    )
    official = metadata("https://youtu.be/Official_123", "OFFICIAL", "Incident clip", 1280)
    resolution = OriginalSourceResolver(
        settings, FakeMetadataProvider(submitted), FakeSearchProvider([official])
    ).resolve(submitted.source_url)
    assert resolution.selected.metadata.source_url == official.source_url
    assert "Ownership classification: OFFICIAL_AGENCY" in resolution.selected.reason
    assert resolution.selected.quality.score > resolution.submitted.quality.score


def test_watermark_rules_are_conservative_for_agency_overlays(tmp_path: Path):
    settings = settings_with_registry(tmp_path)
    service = WatermarkDetectionService(settings)
    official = metadata("https://youtu.be/Official_123", "OFFICIAL", "Bodycam evidence release")
    assert service.detect(official, "OFFICIAL_AGENCY").status == WatermarkStatus.AGENCY_BRANDING
    repost = metadata("https://youtu.be/Repost_123", "REPOST", "Clip", hint="TikTok watermark")
    result = service.detect(repost, "REPOST_ACCOUNT")
    assert result.status == WatermarkStatus.PLATFORM_WATERMARK
    assert result.confidence >= settings.source_watermark_review_threshold


def test_fingerprints_identify_metadata_duplicates_without_blocking():
    original = metadata("https://youtu.be/Original123", "A", "A distinctive incident", 1920)
    copy = metadata("https://youtu.be/CopyVideo123", "B", "A distinctive incident", 1920)
    assert fingerprint_similarity(source_fingerprint(original), source_fingerprint(copy)) >= 0.82


def test_source_decisions_persist_audit_and_reject_stale_selection(session, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings = settings_with_registry(tmp_path)
    submitted = metadata(
        "https://youtu.be/AbCdEf_1234", "REPOST", "Incident repost", hint="watermark"
    )
    official = metadata("https://youtu.be/Official_123", "OFFICIAL", "Incident official")
    resolver = OriginalSourceResolver(
        settings, FakeMetadataProvider(submitted), FakeSearchProvider([official])
    )
    project = create_project(session, DEV_ACTOR_ID, submitted.source_url, resolver=resolver)
    candidates = list(
        session.scalars(
            select(ProductionSource)
            .where(ProductionSource.project_id == project.id)
            .order_by(ProductionSource.quality_score.desc())
        )
    )
    assert project.selected_source_id == candidates[0].id
    assert project.status == "SOURCE_RESOLVED"
    submitted_record = next(
        candidate for candidate in candidates if candidate.source_url == submitted.source_url
    )
    assert any(
        candidate.parent_source_id == submitted_record.id
        for candidate in candidates
        if candidate.id != submitted_record.id
    )
    alternate = candidates[-1]
    chosen = choose_source(
        session, DEV_ACTOR_ID, project, alternate.id, project.source_decision_version
    )
    assert chosen.selected_source_id == alternate.id
    with pytest.raises(ProductionError, match="source selection changed"):
        choose_source(session, DEV_ACTOR_ID, chosen, uuid.uuid4(), 1)
    events = list(
        session.scalars(select(AuditEvent.event_name).where(AuditEvent.entity_id == project.id))
    )
    assert "production.source.submitted" in events
    assert "production.source.candidates_ranked" in events
    assert "production.source.changed_manually" in events
