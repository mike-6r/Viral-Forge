import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Annotated, cast
from urllib.parse import quote

import structlog
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.accounts.auth import Actor, development_actor, require_role
from app.accounts.models import RoleName
from app.analysis.models import (
    AnalysisEvent,
    AnalysisSegment,
    TranscriptSegment,
    VideoAnalysis,
)
from app.analysis.service import cancel_analysis, request_analysis
from app.analytics.models import OperatorFeedbackLabel, PostAnalyticsSnapshot
from app.analytics.service import (
    AnalyticsError,
    NormalizedMetrics,
    add_feedback,
    persist_snapshot,
    refresh_brand,
)
from app.analytics.service import (
    dashboard as analytics_dashboard,
)
from app.audit.models import AuditEvent
from app.brands.models import (
    Brand,
    BrandMembership,
    ContentProfile,
    DestinationAccount,
    SourceAccount,
    Workspace,
)
from app.brands.service import BrandError, brand_for_actor, set_default_brand
from app.common.config import Environment, get_settings
from app.common.db import get_session
from app.common.errors import DomainError
from app.common.logging import configure_logging
from app.content.lifecycle import transition
from app.content.models import ContentItem, ContentStatus, MediaAsset, Platform
from app.content_packages.models import ContentPackage, ContentPackageVersion
from app.content_packages.service import (
    decide_content_package,
    edit_content_package,
    request_content_package_generation,
)
from app.discovery.models import DiscoveredMedia, DiscoveryRun, DiscoverySource
from app.discovery.service import DiscoveryError, approve_media, reject_media, run_source
from app.ingestion.feeds import (
    FeedError,
    change_feed_status,
    ensure_utc,
    get_feed_client,
    next_eligible_run,
    register_feed,
    run_feed,
    validate_feed,
)
from app.ingestion.http import SafeOutboundHttpClient
from app.ingestion.models import (
    FeedEntry,
    FeedSubscription,
    IngestionJob,
    IngestionMethod,
    IngestionStatus,
)
from app.ingestion.service import change_source_status, submit_url
from app.ingestion.storage import LocalFilesystemStorage
from app.ingestion.upload import UploadError, UploadErrorCategory, submit_upload
from app.media_preview.models import PreviewGrant
from app.media_preview.service import (
    PreviewError,
    cleanup_expired_media,
    extend_retention,
    generate_proxy,
    issue_preview,
    parse_range,
    revoke_grants,
    set_hold,
    storage_summary,
    stream_range,
    validate_grant,
)
from app.opportunities.models import (
    ClipOpportunity,
    ClipOpportunityVersion,
    OpportunityGenerationRun,
    OpportunityReason,
)
from app.opportunities.service import (
    decide_opportunity,
    generate_approved_opportunity,
    request_opportunity_generation,
)
from app.producer.models import ClipQualityReport, ProducerRecommendation
from app.producer.service import (
    decide_recommendation,
    generate_clip_quality_report,
    generate_clip_recommendations,
    generate_project_recommendations,
)
from app.production.models import (
    PostingQueueItem,
    ProductionClip,
    ProductionProject,
    ProductionSource,
)
from app.production.service import (
    ProductionError,
    accept_source,
    choose_source,
    create_project,
    decide_clip,
    download_project,
    generate_clips,
    reject_source,
)
from app.publishing.credentials import credential_store
from app.publishing.models import (
    PublishAttempt,
    PublishingAccountConnection,
    PublishRequest,
    PublishReviewGate,
    TikTokCreatorCapability,
)
from app.publishing.service import (
    PublishingError,
    cancel_publish,
    complete_tiktok_draft,
    confirm_publish,
    disconnect_tiktok_connection,
    refresh_tiktok_connection,
    refresh_tiktok_status,
    request_publish,
    request_tiktok_publish,
    set_review_gate,
    verify_destination_connection,
)
from app.publishing.tiktok import (
    TikTokPublishingProvider,
    consume_oauth_state,
    consume_oauth_verifier,
    create_oauth_state,
    oauth_code_challenge,
    persist_capabilities,
)
from app.sources.models import Source, SourcePolicy, SourceStatus, SourceType


class ContentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    description: str | None = None
    source_url: str = Field(min_length=1, max_length=2048)
    source_platform: Platform = Platform.MANUAL
    source_external_id: str | None = Field(default=None, max_length=255)
    uploader_name: str | None = Field(default=None, max_length=255)


class ContentRead(BaseModel):
    id: uuid.UUID
    title: str
    description: str | None
    status: ContentStatus
    source_provenance_complete: bool
    created_at: datetime
    model_config = {"from_attributes": True}


class ContentPage(BaseModel):
    items: list[ContentRead]
    total: int
    page: int
    page_size: int


class TransitionRequest(BaseModel):
    target_status: ContentStatus
    reason: str = Field(min_length=1, max_length=2000)


class AuditRead(BaseModel):
    id: uuid.UUID
    event_name: str
    reason: str | None
    actor_id: uuid.UUID | None
    created_at: datetime
    payload: dict[str, object] | None
    model_config = {"from_attributes": True}


class UrlIngestionRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    source_id: uuid.UUID | None = None
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=255)
    notes: str | None = Field(default=None, max_length=2_000)


class IngestionJobRead(BaseModel):
    id: uuid.UUID
    status: str
    method: str
    requested_url: str | None
    result_content_id: uuid.UUID | None
    created_at: datetime
    normalized_url: str | None = None
    final_url: str | None = None
    selected_metadata: dict[str, str | None] | None = None
    lifecycle_state: ContentStatus | None = None
    warnings: list[str] = Field(default_factory=list)
    correlation_id: str | None = None
    result_metadata: dict[str, object] | None = None


class UploadIngestionRead(BaseModel):
    id: uuid.UUID
    status: str
    content_id: uuid.UUID | None
    asset_id: uuid.UUID | None
    duplicate_outcome: str | None = None
    original_filename: str | None
    detected_media_type: str | None
    file_size_bytes: int | None
    sha256: str | None
    lifecycle_state: ContentStatus | None
    correlation_id: str | None


class FeedCreate(BaseModel):
    source_id: uuid.UUID
    feed_url: str = Field(min_length=1, max_length=2048)
    polling_interval_seconds: int | None = Field(default=None, ge=1, le=86_400)
    recent_item_window_days: int | None = Field(default=None, ge=1, le=365)
    max_items_per_run: int | None = Field(default=None, ge=1, le=1_000)
    notes: str | None = Field(default=None, max_length=2_000)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=255)


class FeedPatch(BaseModel):
    version_id: int = Field(ge=1)
    polling_interval_seconds: int | None = Field(default=None, ge=1, le=86_400)
    recent_item_window_days: int | None = Field(default=None, ge=1, le=365)
    max_items_per_run: int | None = Field(default=None, ge=1, le=1_000)
    notes: str | None = Field(default=None, max_length=2_000)


class FeedRunRequest(BaseModel):
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=255)
    recent_item_window_days: int | None = Field(default=None, ge=1, le=365)
    max_items_per_run: int | None = Field(default=None, ge=1, le=1_000)


class FeedBlockRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2_000)


class FeedRead(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    feed_url: str
    final_url: str | None
    feed_type: str
    status: str
    title: str | None
    description: str | None
    polling_interval_seconds: int
    recent_item_window_days: int
    max_items_per_run: int
    last_checked_at: datetime | None
    last_success_at: datetime | None
    consecutive_failures: int
    correlation_id: str | None
    language: str | None
    site_url: str | None
    last_error_category: str | None
    notes: str | None
    version_id: int
    etag_present: bool = False
    last_modified_present: bool = False
    lease_active: bool = False
    next_eligible_run: datetime | None = None
    model_config = {"from_attributes": True}


class FeedPage(BaseModel):
    items: list[FeedRead]
    total: int
    page: int
    page_size: int
    model_config = {"from_attributes": True}


class FeedEntryRead(BaseModel):
    id: uuid.UUID
    subscription_id: uuid.UUID
    content_id: uuid.UUID | None
    identity_strategy: str
    entry_guid: str
    link: str | None
    title: str | None
    author: str | None
    published_at: datetime | None
    updated_at_source: datetime | None
    import_outcome: str
    failure_category: str | None
    created_at: datetime
    model_config = {"from_attributes": True}


class FeedEntryPage(BaseModel):
    items: list[FeedEntryRead]
    total: int
    page: int
    page_size: int


class FeedRunRead(BaseModel):
    id: uuid.UUID
    status: str
    actor_id: uuid.UUID
    started_at: datetime | None
    completed_at: datetime | None
    error_category: str | None
    error_message: str | None
    correlation_id: str | None
    result_metadata: dict[str, object] | None
    model_config = {"from_attributes": True}


class FeedRunPage(BaseModel):
    items: list[FeedRunRead]
    total: int
    page: int
    page_size: int


class SourceCreate(BaseModel):
    normalized_url: str = Field(min_length=1, max_length=2048)
    source_type: SourceType = SourceType.UNKNOWN


class SourceRead(BaseModel):
    id: uuid.UUID
    normalized_url: str
    status: SourceStatus
    source_type: SourceType
    created_at: datetime
    model_config = {"from_attributes": True}


class ProductionProjectCreate(BaseModel):
    brand_id: uuid.UUID | None = None
    source_url: str = Field(min_length=1, max_length=2_048)
    source_title: str | None = Field(default=None, max_length=500)
    source_channel: str | None = Field(default=None, max_length=500)


class ProductionProjectRead(BaseModel):
    id: uuid.UUID
    brand_id: uuid.UUID
    source_url: str
    source_video_id: str | None
    source_title: str | None
    source_channel: str | None
    source_duration_seconds: float | None
    selected_source_id: uuid.UUID | None
    source_decision_version: int
    status: str
    source_storage_key: str | None
    last_error: str | None
    created_at: datetime
    model_config = {"from_attributes": True}


class ProductionClipRead(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    brand_id: uuid.UUID
    clip_number: int
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    render_status: str
    approval_status: str
    caption: str | None
    publication_status: str
    created_at: datetime
    model_config = {"from_attributes": True}


class ProductionSourceRead(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    platform: str
    source_url: str
    uploader_name: str | None
    account_url: str | None
    video_title: str | None
    upload_date: str | None
    duration_seconds: float | None
    width: int | None
    height: int | None
    ownership_classification: str
    official_source_confidence: float
    original_source_confidence: float
    repost_likelihood: float
    watermark_status: str
    watermark_confidence: float
    quality_score: float
    quality_components: dict[str, float]
    warnings: list[str]
    selected_source_reason: str | None
    quality_status: str
    model_config = {"from_attributes": True}


class SourceChoiceRequest(BaseModel):
    source_id: uuid.UUID
    expected_version: int = Field(ge=1)


class ProductionQueueRead(BaseModel):
    id: uuid.UUID
    clip_id: uuid.UUID
    brand_id: uuid.UUID
    target_platform: str
    status: str
    attempts: int
    published_url: str | None
    last_error: str | None
    model_config = {"from_attributes": True}


class ContentPackageRead(BaseModel):
    id: uuid.UUID
    clip_id: uuid.UUID
    project_id: uuid.UUID
    brand_id: uuid.UUID
    generation_version: int
    status: str
    review_version: int
    provider_name: str
    model_name: str | None
    provider_version: str | None
    language: str
    content_category: str
    confidence: float
    explanation: str
    fields_json: dict[str, object]
    verified_facts_json: list[str]
    transcript_statements_json: list[str]
    uncertainty_json: list[str]
    warnings_json: list[str]
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class ContentPackageVersionRead(BaseModel):
    id: uuid.UUID
    content_package_id: uuid.UUID
    version: int
    status: str
    actor_id: uuid.UUID | None
    action: str
    reason: str | None
    snapshot_json: dict[str, object]
    created_at: datetime
    model_config = {"from_attributes": True}


class ContentPackageEditRequest(BaseModel):
    expected_version: int = Field(ge=1)
    fields_json: dict[str, object]


class ContentPackageDecisionRequest(BaseModel):
    expected_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=2_000)


class ProducerRecommendationRead(BaseModel):
    id: uuid.UUID
    brand_id: uuid.UUID
    discovered_media_id: uuid.UUID | None
    project_id: uuid.UUID | None
    clip_id: uuid.UUID | None
    content_package_id: uuid.UUID | None
    recommendation_type: str
    status: str
    confidence: float
    reasoning: str
    evidence_json: list[dict[str, object]]
    recommendation_json: dict[str, object]
    operator_edit_json: dict[str, object]
    prediction_json: dict[str, object]
    provider_name: str
    model_name: str | None
    provider_version: str | None
    review_version: int
    decided_by_id: uuid.UUID | None
    decision_reason: str | None
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class ProducerDecisionRequest(BaseModel):
    expected_version: int = Field(ge=1)
    operator_edit_json: dict[str, object] = Field(default_factory=dict)
    reason: str | None = Field(default=None, max_length=2_000)


class ClipQualityReportRead(BaseModel):
    id: uuid.UUID
    brand_id: uuid.UUID
    project_id: uuid.UUID
    clip_id: uuid.UUID
    report_version: int
    hook_quality: float
    pacing_quality: float
    context_quality: float
    retention_estimate: float
    subtitle_quality: float
    title_quality: float
    caption_quality: float
    hashtag_quality: float
    overall_readiness: float
    reasoning: str
    evidence_json: list[dict[str, object]]
    recommendations_json: dict[str, object]
    prediction_json: dict[str, object]
    provider_name: str
    model_name: str | None
    provider_version: str | None
    created_at: datetime
    model_config = {"from_attributes": True}


class VideoAnalysisRead(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    brand_id: uuid.UUID
    source_id: uuid.UUID | None
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    duration_seconds: float | None
    fps: float | None
    width: int | None
    height: int | None
    frame_count: int | None
    transcript_language: str | None
    analysis_version: str
    current_stage: str | None
    progress_percent: float
    metadata_json: dict[str, object]
    model_config = {"from_attributes": True}


class AnalysisSegmentRead(BaseModel):
    id: uuid.UUID
    start_time: float
    end_time: float
    segment_type: str
    confidence: float | None
    score: float | None
    metadata_json: dict[str, object]
    model_config = {"from_attributes": True}


class TranscriptSegmentRead(BaseModel):
    id: uuid.UUID
    start_time: float
    end_time: float
    speaker: str | None
    text: str
    confidence: float | None
    metadata_json: dict[str, object]
    model_config = {"from_attributes": True}


class AnalysisStartRequest(BaseModel):
    analysis_version: str | None = Field(default=None, min_length=1, max_length=100)


class AnalysisEventRead(BaseModel):
    id: uuid.UUID
    timestamp: float
    event_type: str
    confidence: float | None
    metadata_json: dict[str, object]
    model_config = {"from_attributes": True}


class OpportunityGenerationRunRead(BaseModel):
    id: uuid.UUID
    analysis_id: uuid.UUID
    project_id: uuid.UUID
    brand_id: uuid.UUID
    generation_version: int
    status: str
    provider_name: str
    started_at: datetime | None
    completed_at: datetime | None
    opportunity_count: int
    error_summary: str | None
    model_config = {"from_attributes": True}


class ClipOpportunityRead(BaseModel):
    id: uuid.UUID
    analysis_id: uuid.UUID
    project_id: uuid.UUID
    brand_id: uuid.UUID
    generation_version: int
    start_time: float
    end_time: float
    duration_seconds: float
    confidence: float
    overall_score: float
    review_status: str
    generation_status: str
    review_version: int
    overlap_percentage: float
    explanation: str
    generated_clip_id: uuid.UUID | None
    generation_error: str | None
    model_config = {"from_attributes": True}


class OpportunityReasonRead(BaseModel):
    id: uuid.UUID
    reason_type: str
    score: float
    weight: float
    metadata_json: dict[str, object]
    model_config = {"from_attributes": True}


class ClipOpportunityVersionRead(BaseModel):
    id: uuid.UUID
    version: int
    review_status: str
    generation_status: str
    actor_id: uuid.UUID | None
    decision_reason: str | None
    created_at: datetime
    model_config = {"from_attributes": True}


class OpportunityDecisionRequest(BaseModel):
    expected_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=2_000)


class DiscoverySourceCreate(BaseModel):
    brand_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=500)
    provider: str = Field(min_length=1, max_length=50)
    source_type: str = Field(min_length=1, max_length=50)
    platform: str = Field(min_length=1, max_length=50)
    public_url: str = Field(min_length=1, max_length=2048)
    agency_reference: str | None = Field(default=None, max_length=500)
    account_identifier: str | None = Field(default=None, max_length=500)
    enabled: bool = True
    trusted: bool = False
    polling_interval_seconds: int | None = Field(default=None, ge=60, le=86_400)
    configuration_json: dict[str, object] = Field(default_factory=dict)


class DiscoverySourceRead(BaseModel):
    id: uuid.UUID
    brand_id: uuid.UUID
    name: str
    provider: str
    source_type: str
    platform: str
    public_url: str
    enabled: bool
    trusted: bool
    polling_interval_seconds: int
    failure_count: int
    last_error_category: str | None
    next_poll_at: datetime | None
    configuration_json: dict[str, object]
    model_config = {"from_attributes": True}


class DiscoveredMediaRead(BaseModel):
    id: uuid.UUID
    discovery_source_id: uuid.UUID
    brand_id: uuid.UUID
    provider_item_id: str
    canonical_url: str
    title: str | None
    uploader: str | None
    published_at: datetime | None
    discovery_score: float
    quality_score: float | None
    source_confidence: float | None
    watermark_status: str
    duplicate_status: str
    lifecycle_status: str
    production_project_id: uuid.UUID | None
    review_version: int
    metadata_json: dict[str, object]
    model_config = {"from_attributes": True}


class DiscoveryRunRead(BaseModel):
    id: uuid.UUID
    provider: str
    discovery_source_id: uuid.UUID
    brand_id: uuid.UUID
    status: str
    fetched_count: int
    new_count: int
    duplicate_count: int
    skipped_count: int
    failed_count: int
    error_summary: str | None
    started_at: datetime
    finished_at: datetime | None
    cursor: str | None
    model_config = {"from_attributes": True}


class DiscoveryDecisionRequest(BaseModel):
    expected_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=2000)


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")
    timezone: str = Field(default="UTC", min_length=1, max_length=100)


class WorkspaceRead(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    timezone: str
    is_legacy: bool
    model_config = {"from_attributes": True}


class BrandCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")
    description: str | None = Field(default=None, max_length=4000)


class BrandRead(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    slug: str
    description: str | None
    is_active: bool
    is_legacy: bool
    model_config = {"from_attributes": True}


class BrandMembershipCreate(BaseModel):
    user_id: uuid.UUID
    role: str = Field(default="VIEWER", min_length=1, max_length=50)
    is_default: bool = False


class BrandMembershipRead(BaseModel):
    id: uuid.UUID
    brand_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    is_default: bool
    model_config = {"from_attributes": True}


class ContentProfilePatch(BaseModel):
    niche_name: str | None = Field(default=None, max_length=255)
    discovery_categories: list[str] | None = None
    included_keywords: list[str] | None = None
    excluded_keywords: list[str] | None = None
    preferred_source_providers: list[str] | None = None
    min_clip_duration_seconds: int | None = Field(default=None, ge=1, le=600)
    max_clip_duration_seconds: int | None = Field(default=None, ge=1, le=600)
    opportunity_weights_json: dict[str, float] | None = None
    opportunity_profile_reference: str | None = Field(default=None, max_length=255)
    caption_tone: str | None = Field(default=None, max_length=255)
    title_style: str | None = Field(default=None, max_length=255)
    hashtag_rules: dict[str, object] | None = None
    branding_behavior: dict[str, object] | None = None
    review_requirements: dict[str, object] | None = None
    maximum_posts_per_day: int | None = Field(default=None, ge=0, le=10000)
    target_platforms: list[str] | None = None
    language: str | None = Field(default=None, max_length=50)
    timezone: str | None = Field(default=None, max_length=100)


class ContentProfileRead(ContentProfilePatch):
    id: uuid.UUID
    brand_id: uuid.UUID
    niche_name: str
    model_config = {"from_attributes": True}


class SourceAccountCreate(BaseModel):
    provider: str = Field(min_length=1, max_length=50)
    account_reference: str = Field(min_length=1, max_length=500)
    public_url: str | None = Field(default=None, max_length=2048)
    display_name: str | None = Field(default=None, max_length=500)
    provider_metadata: dict[str, object] = Field(default_factory=dict)


class SourceAccountRead(SourceAccountCreate):
    id: uuid.UUID
    brand_id: uuid.UUID
    is_active: bool
    model_config = {"from_attributes": True}


class DestinationAccountCreate(BaseModel):
    provider: str = Field(min_length=1, max_length=50)
    account_reference: str = Field(min_length=1, max_length=500)
    credential_reference_id: str | None = Field(default=None, max_length=500)
    display_name: str | None = Field(default=None, max_length=500)
    provider_metadata: dict[str, object] = Field(default_factory=dict)


class DestinationAccountRead(DestinationAccountCreate):
    id: uuid.UUID
    brand_id: uuid.UUID
    is_active: bool
    model_config = {"from_attributes": True}


class PublishReviewGateRequest(BaseModel):
    rights_required: bool = False
    rights_disposition: str = Field(
        default="NOT_APPLICABLE", pattern=r"^(APPROVED|REJECTED|NOT_APPLICABLE)$"
    )
    moderation_disposition: str = Field(default="PENDING", pattern=r"^(APPROVED|REJECTED|PENDING)$")
    notes: str | None = Field(default=None, max_length=2000)


class PublishReviewGateRead(BaseModel):
    id: uuid.UUID
    clip_id: uuid.UUID
    brand_id: uuid.UUID
    rights_required: bool
    rights_disposition: str
    moderation_disposition: str
    notes: str | None
    model_config = {"from_attributes": True}


class DestinationConnectionRead(BaseModel):
    id: uuid.UUID
    destination_account_id: uuid.UUID
    connection_state: str
    provider_account_id: str | None
    provider_channel_url: str | None
    checked_at: str | None
    last_error_category: str | None
    last_error_summary: str | None
    model_config = {"from_attributes": True}


class PublishRequestCreate(BaseModel):
    clip_id: uuid.UUID
    content_package_id: uuid.UUID
    destination_account_id: uuid.UUID
    decision_type: str = Field(pattern=r"^(MANUAL|SCHEDULED)$")
    scheduled_for: datetime | None = None
    idempotency_key: str = Field(min_length=8, max_length=255)


class PublishRequestRead(BaseModel):
    id: uuid.UUID
    brand_id: uuid.UUID
    queue_item_id: uuid.UUID
    clip_id: uuid.UUID
    content_package_id: uuid.UUID
    destination_account_id: uuid.UUID
    requested_by_id: uuid.UUID
    confirmed_by_id: uuid.UUID | None
    decision_type: str
    status: str
    scheduled_for: str | None
    platform_metadata: dict[str, object]
    upload_progress_percent: int
    attempt_count: int
    next_attempt_at: str | None
    failure_category: str | None
    failure_summary: str | None
    remote_post_id: str | None
    remote_post_url: str | None
    cancelled_before_upload: bool
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class PublishAttemptRead(BaseModel):
    id: uuid.UUID
    publish_request_id: uuid.UUID
    attempt_number: int
    status: str
    failure_category: str | None
    detail: str | None
    remote_post_id: str | None
    remote_post_url: str | None
    created_at: datetime
    model_config = {"from_attributes": True}


class TikTokOAuthStart(BaseModel):
    destination_account_id: uuid.UUID
    requested_scopes: list[str] = Field(default_factory=lambda: ["user.info.basic", "video.upload"])


class TikTokConnectionStatusRead(DestinationConnectionRead):
    granted_scopes: list[str] = Field(default_factory=list)
    application_review_state: str


class TikTokCapabilityRead(BaseModel):
    id: uuid.UUID
    destination_account_id: uuid.UUID
    brand_id: uuid.UUID
    creator_identity_reference: str
    creator_username: str | None
    creator_nickname: str | None
    privacy_options: list[str]
    max_video_duration_seconds: int | None
    comments_disabled: bool
    duet_disabled: bool
    stitch_disabled: bool
    captured_at: str
    model_config = {"from_attributes": True}


class TikTokPublishRequestCreate(BaseModel):
    clip_id: uuid.UUID
    content_package_id: uuid.UUID
    destination_account_id: uuid.UUID
    idempotency_key: str = Field(min_length=8, max_length=255)
    privacy_level: str | None = Field(default=None, max_length=64)


class AnalyticsSnapshotImport(BaseModel):
    views: int | None = Field(default=None, ge=0)
    watch_time_seconds: float | None = Field(default=None, ge=0)
    average_view_duration_seconds: float | None = Field(default=None, ge=0)
    retention_percentage: float | None = Field(default=None, ge=0, le=100)
    likes: int | None = Field(default=None, ge=0)
    comments: int | None = Field(default=None, ge=0)
    shares: int | None = Field(default=None, ge=0)
    saves: int | None = Field(default=None, ge=0)
    followers_gained: int | None = Field(default=None, ge=0)
    clicks: int | None = Field(default=None, ge=0)
    platform_revenue: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=10)
    raw_metadata: dict[str, object] = Field(default_factory=dict)


class AnalyticsSnapshotRead(AnalyticsSnapshotImport):
    id: uuid.UUID
    publish_request_id: uuid.UUID
    clip_id: uuid.UUID
    brand_id: uuid.UUID
    provider: str
    captured_at: datetime
    collection_source: str
    model_config = {"from_attributes": True}


class FeedbackCreate(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=255)
    notes: str | None = Field(default=None, max_length=2000)


class FeedbackRead(FeedbackCreate):
    id: uuid.UUID
    publish_request_id: uuid.UUID
    clip_id: uuid.UUID
    brand_id: uuid.UUID
    actor_id: uuid.UUID
    model_config = {"from_attributes": True}


class PreviewGrantRead(BaseModel):
    id: uuid.UUID
    clip_id: uuid.UUID
    media_asset_id: uuid.UUID
    expires_at: datetime
    revoked_at: datetime | None
    access_count: int
    maximum_access_count: int | None
    reused: bool = False
    url: str | None = None
    model_config = {"from_attributes": True}


class RetentionAction(BaseModel):
    seconds: int = Field(default=86_400, ge=60, le=31_536_000)


class DeleteNowRequest(BaseModel):
    confirm: bool = False


class CleanupRead(BaseModel):
    selected: int
    deleted: int
    reclaimed_bytes: int
    failures: int
    dry_run: bool


def get_production_storage() -> LocalFilesystemStorage:
    return LocalFilesystemStorage(Path(get_settings().local_storage_root))


def require_record_brand(session: Session, actor: Actor, record: object) -> None:
    brand_id = getattr(record, "brand_id", None)
    if not isinstance(brand_id, uuid.UUID):
        raise HTTPException(status_code=404, detail="brand-scoped record not found")
    brand_for_actor(session, actor.id, actor.roles, brand_id)


def require_reference_only_metadata(metadata: dict[str, object]) -> None:
    forbidden = {"token", "secret", "password", "api_key", "apikey", "credential", "cookie"}
    if any(str(key).lower().replace("-", "_") in forbidden for key in metadata):
        raise HTTPException(
            status_code=422, detail="credentials must be stored outside account metadata"
        )


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="ViralForge", version="0.1.0")
    settings = get_settings()

    def require_trusted_https_feature() -> None:
        try:
            get_settings().require_trusted_https_feature()
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    if settings.trusted_host_list():
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_host_list())
    if settings.cors_origin_list():
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list(),
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Correlation-ID"],
        )

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        correlation_id = request.headers.get("X-Correlation-ID", request_id)
        structlog.contextvars.bind_contextvars(request_id=request_id, correlation_id=correlation_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Correlation-ID"] = correlation_id
            return response
        finally:
            structlog.contextvars.clear_contextvars()

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, error: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": {"code": "domain_error", "message": str(error)}},
        )

    @app.exception_handler(UploadError)
    async def upload_error(_: Request, error: UploadError) -> JSONResponse:
        status_code = {
            UploadErrorCategory.FILE_TOO_LARGE: status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            UploadErrorCategory.UNSUPPORTED_CONTENT_TYPE: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            UploadErrorCategory.INVALID_FILE_SIGNATURE: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            UploadErrorCategory.MIME_MISMATCH: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            UploadErrorCategory.IDEMPOTENCY_CONFLICT: status.HTTP_409_CONFLICT,
            UploadErrorCategory.POLICY_VIOLATION: status.HTTP_403_FORBIDDEN,
            UploadErrorCategory.SOURCE_INACTIVE: status.HTTP_403_FORBIDDEN,
            UploadErrorCategory.SOURCE_NOT_ALLOWED: status.HTTP_403_FORBIDDEN,
        }.get(error.category, status.HTTP_400_BAD_REQUEST)
        return JSONResponse(
            status_code=status_code,
            content={"error": {"code": error.category.value.lower(), "message": str(error)}},
        )

    @app.exception_handler(FeedError)
    async def feed_error(_: Request, error: FeedError) -> JSONResponse:
        code = (
            status.HTTP_409_CONFLICT
            if error.category
            in {"FEED_ALREADY_RUNNING", "FEED_RUN_TOO_SOON", "IDEMPOTENCY_CONFLICT"}
            else status.HTTP_403_FORBIDDEN
            if error.category in {"SOURCE_INACTIVE", "POLICY_VIOLATION", "FEED_INACTIVE"}
            else status.HTTP_400_BAD_REQUEST
        )
        return JSONResponse(
            status_code=code,
            content={"error": {"code": error.category.lower(), "message": str(error)}},
        )

    @app.exception_handler(ProductionError)
    async def production_error(_: Request, error: ProductionError) -> JSONResponse:
        status_code = (
            status.HTTP_409_CONFLICT
            if error.code
            in {
                "SOURCE_NOT_READY",
                "ANALYSIS_SOURCE_NOT_READY",
                "ANALYSIS_ALREADY_RUNNING",
                "ANALYSIS_NOT_READY",
                "OPPORTUNITY_GENERATION_RUNNING",
                "STALE_OPPORTUNITY_ACTION",
                "STALE_OPPORTUNITY",
                "OPPORTUNITY_NOT_APPROVED",
                "CLIPS_ALREADY_GENERATED",
                "CLIP_NOT_RENDERED",
            }
            else status.HTTP_400_BAD_REQUEST
        )
        return JSONResponse(
            status_code=status_code,
            content={"error": {"code": error.code.lower(), "message": str(error)}},
        )

    @app.exception_handler(PublishingError)
    async def publishing_error(_: Request, error: PublishingError) -> JSONResponse:
        status_code = (
            status.HTTP_409_CONFLICT
            if error.code.endswith("_REQUIRED")
            or error.code in {"IDEMPOTENCY_CONFLICT", "PUBLISH_CANCELLED", "UPLOAD_ALREADY_STARTED"}
            else status.HTTP_422_UNPROCESSABLE_ENTITY
            if error.code
            in {"INVALID_PUBLISH_DECISION", "SCHEDULE_REQUIRED", "INVALID_REVIEW_GATE"}
            else status.HTTP_400_BAD_REQUEST
        )
        return JSONResponse(
            status_code=status_code,
            content={"error": {"code": error.code.lower(), "message": error.message}},
        )

    @app.exception_handler(AnalyticsError)
    async def analytics_error(_: Request, error: AnalyticsError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": {"code": error.code.lower(), "message": error.message}},
        )

    @app.exception_handler(BrandError)
    async def brand_error(_: Request, error: BrandError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN
            if error.code == "BRAND_ACCESS_DENIED"
            else status.HTTP_404_NOT_FOUND,
            content={"error": {"code": error.code.lower(), "message": str(error)}},
        )

    @app.exception_handler(DiscoveryError)
    async def discovery_error(_: Request, error: DiscoveryError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": {"code": error.code.lower(), "message": str(error)}},
        )

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, error: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={"error": {"code": "http_error", "message": str(error.detail)}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "request validation failed",
                    "details": error.errors(),
                }
            },
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    def ready(session: Annotated[Session, Depends(get_session)]) -> dict[str, str]:
        session.execute(select(1))
        return {"status": "ready"}

    @app.get("/api/v1/system/info")
    def system_info() -> dict[str, str]:
        settings = get_settings()
        return {
            "service": settings.service_name,
            "environment": settings.environment.value,
            "deployment_mode": settings.deployment_mode.value,
            "version": "0.1.0",
        }

    def preview_failure(error: PreviewError) -> HTTPException:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="preview is unavailable")

    @app.get("/preview/{preview_id}", response_class=HTMLResponse)
    def browser_preview(
        preview_id: uuid.UUID,
        token: str | None = Query(default=None, max_length=512),
        session: Annotated[Session, Depends(get_session)] = None,  # type: ignore[assignment]
    ) -> HTMLResponse:
        """Private, no-cache player shell.  Media is separately token validated."""
        try:
            grant, asset, clip, project = validate_grant(session, preview_id, token)
        except PreviewError as error:
            raise preview_failure(error) from error
        source = (
            session.get(ProductionSource, project.selected_source_id)
            if project.selected_source_id
            else None
        )
        title = project.source_title or f"Clip {clip.clip_number}"
        attribution = (
            source.uploader_name
            if source and source.uploader_name
            else (project.source_channel or "Source attribution unavailable")
        )
        expires = grant.expires_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
        media_url = f"/api/v1/previews/{preview_id}/media?token={quote(token or '', safe='')}"
        document = f"""<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><meta name=\"robots\" content=\"noindex,nofollow,noarchive\"><title>{escape(get_settings().preview_page_title)}</title><style>body{{margin:0;background:#101217;color:#edf0f5;font:16px system-ui,sans-serif}}main{{max-width:760px;margin:auto;padding:24px}}video{{width:100%;max-height:78vh;background:#000;border-radius:10px}}.meta{{color:#b6c0d0;font-size:.92rem}}h1{{font-size:1.25rem}}</style></head><body><main><h1>{escape(title)}</h1><p class=\"meta\">{escape(project.source_platform)} · {clip.duration_seconds:.1f}s · {escape(attribution)}</p><video controls playsinline preload=\"metadata\"><source src=\"{escape(media_url, quote=True)}\" type=\"{escape(asset.content_type or "video/mp4", quote=True)}\">Your browser cannot play this preview.</video><p class=\"meta\">Private review preview. Expires {escape(expires)}.</p></main></body></html>"""
        response = HTMLResponse(document)
        response.headers.update(
            {
                "Cache-Control": "private, no-store",
                "X-Robots-Tag": "noindex, nofollow, noarchive",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; media-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
                "X-Frame-Options": "DENY",
            }
        )
        return response

    @app.api_route("/api/v1/previews/{preview_id}/media", methods=["GET", "HEAD"])
    def preview_media(
        preview_id: uuid.UUID,
        request: Request,
        token: str | None = Query(default=None, max_length=512),
        session: Annotated[Session, Depends(get_session)] = None,  # type: ignore[assignment]
        storage: Annotated[LocalFilesystemStorage, Depends(get_production_storage)] = None,  # type: ignore[assignment]
    ) -> Response:
        try:
            _, asset, _, _ = validate_grant(
                session, preview_id, token, count_access=request.method == "GET"
            )
            object_meta = storage.metadata(asset.storage_key)
            byte_range = parse_range(request.headers.get("range"), object_meta.size_bytes)
        except (PreviewError, FileNotFoundError, ValueError) as error:
            if isinstance(error, PreviewError) and error.code == "INVALID_RANGE":
                return Response(
                    status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                    headers={"Content-Range": "bytes */0", "Cache-Control": "private, no-store"},
                )
            raise preview_failure(
                error
                if isinstance(error, PreviewError)
                else PreviewError("UNAVAILABLE_PREVIEW", "preview is unavailable")
            ) from error
        start, end = byte_range if byte_range else (0, object_meta.size_bytes - 1)
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(end - start + 1),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        }
        response_status = status.HTTP_206_PARTIAL_CONTENT if byte_range else status.HTTP_200_OK
        if byte_range:
            headers["Content-Range"] = f"bytes {start}-{end}/{object_meta.size_bytes}"
        if request.method == "HEAD":
            return Response(
                status_code=response_status,
                headers=headers,
                media_type=asset.content_type or "video/mp4",
            )
        return StreamingResponse(
            stream_range(
                storage.open(asset.storage_key),
                start,
                end,
                get_settings().preview_stream_chunk_bytes,
            ),
            status_code=response_status,
            headers=headers,
            media_type=asset.content_type or "video/mp4",
        )

    @app.post("/api/v1/production/clips/{clip_id}/preview", response_model=PreviewGrantRead)
    def create_clip_preview(
        clip_id: uuid.UUID,
        refresh: bool = False,
        actor: Annotated[Actor, Depends(development_actor)] = None,  # type: ignore[assignment]
        session: Annotated[Session, Depends(get_session)] = None,  # type: ignore[assignment]
        storage: Annotated[LocalFilesystemStorage, Depends(get_production_storage)] = None,  # type: ignore[assignment]
    ) -> PreviewGrantRead:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR, RoleName.REVIEWER)
        clip = session.get(ProductionClip, clip_id)
        if clip is None:
            raise HTTPException(status_code=404, detail="production clip not found")
        require_record_brand(session, actor, clip)
        try:
            issued = issue_preview(session, actor.id, clip, storage, refresh=refresh)
        except PreviewError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return PreviewGrantRead.model_validate(issued.grant, from_attributes=True).model_copy(
            update={"reused": issued.reused, "url": issued.url or None}
        )

    @app.get("/api/v1/production/clips/{clip_id}/preview", response_model=list[PreviewGrantRead])
    def list_clip_previews(
        clip_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> list[PreviewGrant]:
        clip = session.get(ProductionClip, clip_id)
        if clip is None:
            raise HTTPException(status_code=404, detail="production clip not found")
        require_record_brand(session, actor, clip)
        return list(
            session.scalars(
                select(PreviewGrant)
                .where(PreviewGrant.clip_id == clip.id)
                .order_by(PreviewGrant.created_at.desc())
                .limit(100)
            )
        )

    @app.post("/api/v1/production/clips/{clip_id}/preview/revoke")
    def revoke_clip_previews(
        clip_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> dict[str, int]:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR)
        clip = session.get(ProductionClip, clip_id)
        if clip is None:
            raise HTTPException(status_code=404, detail="production clip not found")
        require_record_brand(session, actor, clip)
        return {"revoked": revoke_grants(session, actor.id, clip)}

    @app.get("/api/v1/media/storage-summary")
    def media_storage_summary(
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
        storage: Annotated[LocalFilesystemStorage, Depends(get_production_storage)],
    ) -> dict[str, object]:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN)
        return storage_summary(session, storage)

    @app.post("/api/v1/media/cleanup", response_model=CleanupRead)
    def media_cleanup(
        dry_run: bool = True,
        actor: Annotated[Actor, Depends(development_actor)] = None,  # type: ignore[assignment]
        session: Annotated[Session, Depends(get_session)] = None,  # type: ignore[assignment]
        storage: Annotated[LocalFilesystemStorage, Depends(get_production_storage)] = None,  # type: ignore[assignment]
    ) -> CleanupRead:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN)
        return CleanupRead(**cleanup_expired_media(session, storage, dry_run=dry_run).__dict__)

    @app.post("/api/v1/media/assets/{asset_id}/retention", response_model=dict)
    def extend_media_retention(
        asset_id: uuid.UUID,
        payload: RetentionAction,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> dict[str, object]:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR)
        asset = session.get(MediaAsset, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="media asset not found")
        require_record_brand(session, actor, asset)
        return {"id": str(extend_retention(session, actor.id, asset, payload.seconds).id)}

    @app.post("/api/v1/media/assets/{asset_id}/hold", response_model=dict)
    def set_media_hold(
        asset_id: uuid.UUID,
        enabled: bool,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> dict[str, object]:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN)
        asset = session.get(MediaAsset, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="media asset not found")
        require_record_brand(session, actor, asset)
        set_hold(session, actor.id, asset, enabled)
        return {"id": str(asset.id), "administrative_hold": enabled}

    @app.post("/api/v1/production/clips/{clip_id}/preview-proxy", response_model=dict)
    def make_preview_proxy(
        clip_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
        storage: Annotated[LocalFilesystemStorage, Depends(get_production_storage)],
    ) -> dict[str, object]:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR)
        clip = session.get(ProductionClip, clip_id)
        if clip is None:
            raise HTTPException(status_code=404, detail="production clip not found")
        require_record_brand(session, actor, clip)
        proxy = generate_proxy(session, actor.id, clip, storage)
        return {"generated": proxy is not None, "asset_id": str(proxy.id) if proxy else None}

    @app.post(
        "/api/v1/workspaces", response_model=WorkspaceRead, status_code=status.HTTP_201_CREATED
    )
    def create_workspace(
        payload: WorkspaceCreate,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> Workspace:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN)
        workspace = Workspace(**payload.model_dump())
        session.add(workspace)
        session.flush()
        session.add(
            AuditEvent(
                actor_id=actor.id,
                entity_type="workspace",
                entity_id=workspace.id,
                event_name="workspace.created",
            )
        )
        session.commit()
        return workspace

    @app.get("/api/v1/workspaces", response_model=list[WorkspaceRead])
    def list_workspaces(
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> list[Workspace]:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN)
        return list(session.scalars(select(Workspace).order_by(Workspace.created_at)))

    @app.post(
        "/api/v1/workspaces/{workspace_id}/brands",
        response_model=BrandRead,
        status_code=status.HTTP_201_CREATED,
    )
    def create_brand(
        workspace_id: uuid.UUID,
        payload: BrandCreate,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> Brand:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN)
        if session.get(Workspace, workspace_id) is None:
            raise HTTPException(status_code=404, detail="workspace not found")
        brand = Brand(workspace_id=workspace_id, **payload.model_dump())
        session.add(brand)
        session.flush()
        session.add_all(
            [
                BrandMembership(
                    brand_id=brand.id, user_id=actor.id, role="ADMIN", is_default=False
                ),
                ContentProfile(brand_id=brand.id, niche_name=brand.name),
            ]
        )
        session.add(
            AuditEvent(
                actor_id=actor.id,
                entity_type="brand",
                entity_id=brand.id,
                event_name="brand.created",
                brand_id=brand.id,
            )
        )
        session.commit()
        return brand

    @app.get("/api/v1/brands", response_model=list[BrandRead])
    def list_brands(
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> list[Brand]:
        require_role(
            actor,
            RoleName.OWNER,
            RoleName.ADMIN,
            RoleName.EDITOR,
            RoleName.REVIEWER,
            RoleName.ANALYST,
            RoleName.VIEWER,
        )
        if RoleName.OWNER in actor.roles or RoleName.ADMIN in actor.roles:
            return list(session.scalars(select(Brand).where(Brand.is_active).order_by(Brand.name)))
        return list(
            session.scalars(
                select(Brand)
                .join(BrandMembership)
                .where(BrandMembership.user_id == actor.id, Brand.is_active)
                .order_by(Brand.name)
            )
        )

    @app.get("/api/v1/brands/default", response_model=BrandRead)
    def default_brand(
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> Brand:
        return brand_for_actor(session, actor.id, actor.roles)

    @app.post("/api/v1/brands/{brand_id}/select", response_model=BrandMembershipRead)
    def select_default_brand(
        brand_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> BrandMembership:
        brand_for_actor(session, actor.id, actor.roles, brand_id)
        return set_default_brand(session, actor.id, brand_id)

    @app.post(
        "/api/v1/brands/{brand_id}/members",
        response_model=BrandMembershipRead,
        status_code=status.HTTP_201_CREATED,
    )
    def add_brand_member(
        brand_id: uuid.UUID,
        payload: BrandMembershipCreate,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> BrandMembership:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN)
        brand_for_actor(session, actor.id, actor.roles, brand_id)
        membership = BrandMembership(brand_id=brand_id, **payload.model_dump())
        session.add(membership)
        session.commit()
        return membership

    @app.get("/api/v1/brands/{brand_id}/content-profile", response_model=ContentProfileRead)
    def get_content_profile(
        brand_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> ContentProfile:
        brand_for_actor(session, actor.id, actor.roles, brand_id)
        profile = session.scalar(select(ContentProfile).where(ContentProfile.brand_id == brand_id))
        if profile is None:
            raise HTTPException(status_code=404, detail="content profile not found")
        return profile

    @app.patch("/api/v1/brands/{brand_id}/content-profile", response_model=ContentProfileRead)
    def update_content_profile(
        brand_id: uuid.UUID,
        payload: ContentProfilePatch,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> ContentProfile:
        brand_for_actor(session, actor.id, actor.roles, brand_id)
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR)
        profile = session.scalar(select(ContentProfile).where(ContentProfile.brand_id == brand_id))
        if profile is None:
            profile = ContentProfile(brand_id=brand_id, niche_name=payload.niche_name or "unnamed")
            session.add(profile)
        for name, value in payload.model_dump(exclude_unset=True).items():
            setattr(profile, name, value)
        session.flush()
        session.add(
            AuditEvent(
                actor_id=actor.id,
                entity_type="content_profile",
                entity_id=profile.id,
                event_name="brand.content_profile.updated",
                brand_id=brand_id,
            )
        )
        session.commit()
        return profile

    @app.post(
        "/api/v1/brands/{brand_id}/source-accounts",
        response_model=SourceAccountRead,
        status_code=status.HTTP_201_CREATED,
    )
    def create_source_account(
        brand_id: uuid.UUID,
        payload: SourceAccountCreate,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> SourceAccount:
        brand_for_actor(session, actor.id, actor.roles, brand_id)
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR)
        require_reference_only_metadata(payload.provider_metadata)
        account = SourceAccount(brand_id=brand_id, **payload.model_dump())
        session.add(account)
        session.commit()
        return account

    @app.post(
        "/api/v1/brands/{brand_id}/destination-accounts",
        response_model=DestinationAccountRead,
        status_code=status.HTTP_201_CREATED,
    )
    def create_destination_account(
        brand_id: uuid.UUID,
        payload: DestinationAccountCreate,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> DestinationAccount:
        require_trusted_https_feature()
        brand_for_actor(session, actor.id, actor.roles, brand_id)
        require_role(actor, RoleName.OWNER, RoleName.ADMIN)
        require_reference_only_metadata(payload.provider_metadata)
        account = DestinationAccount(brand_id=brand_id, **payload.model_dump())
        session.add(account)
        session.commit()
        return account

    @app.post("/api/v1/content", response_model=ContentRead, status_code=status.HTTP_201_CREATED)
    def create_content(
        payload: ContentCreate,
        request: Request,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> ContentItem:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR)
        source = Source(
            platform=payload.source_platform,
            external_id=payload.source_external_id,
            normalized_url=payload.source_url.strip().lower(),
            uploader_name=payload.uploader_name,
        )
        item = ContentItem(
            title=payload.title, description=payload.description, source_provenance_complete=True
        )
        session.add_all([source, item])
        try:
            session.flush()
            from app.content.models import ContentSource

            session.add(
                ContentSource(
                    content_id=item.id, source_id=source.id, source_url=source.normalized_url
                )
            )
            session.add(
                AuditEvent(
                    actor_id=actor.id,
                    entity_type="content_item",
                    entity_id=item.id,
                    event_name="content.created",
                    correlation_id=request.headers.get("X-Correlation-ID"),
                    payload={"source_id": str(source.id)},
                )
            )
            session.commit()
        except IntegrityError as error:
            session.rollback()
            raise HTTPException(
                status_code=409,
                detail="a source with this URL or platform external ID already exists",
            ) from error
        session.refresh(item)
        return item

    @app.post(
        "/api/v1/ingestion/url",
        response_model=IngestionJobRead,
        status_code=status.HTTP_201_CREATED,
    )
    def ingest_url(
        payload: UrlIngestionRequest,
        request: Request,
        response: Response,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> IngestionJobRead:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR)
        key = payload.idempotency_key or str(uuid.uuid4())
        existing = session.scalar(select(IngestionJob).where(IngestionJob.idempotency_key == key))
        try:
            job = submit_url(
                session,
                actor.id,
                payload.url,
                key,
                request.headers.get("X-Correlation-ID"),
                payload.source_id,
                payload.notes,
            )
            session.refresh(job)
            source = session.get(Source, job.source_id) if job.source_id is not None else None
            raw_metadata = source.provider_metadata or {} if source is not None else {}
            candidate_metadata = raw_metadata.get("manual_url_ingestion", {})
            ingestion_metadata = candidate_metadata if isinstance(candidate_metadata, dict) else {}
            item = (
                session.get(ContentItem, job.result_content_id)
                if job.result_content_id is not None
                else None
            )
            if existing is not None:
                response.status_code = status.HTTP_200_OK
            elif job.status is IngestionStatus.RETRY_SCHEDULED:
                response.status_code = status.HTTP_504_GATEWAY_TIMEOUT
            elif job.status is IngestionStatus.FAILED:
                response.status_code = (
                    status.HTTP_403_FORBIDDEN
                    if job.error_category == "POLICY_VIOLATION"
                    else status.HTTP_502_BAD_GATEWAY
                )
            return IngestionJobRead(
                id=job.id,
                status=job.status.value,
                method=job.method.value,
                requested_url=job.requested_url,
                result_content_id=job.result_content_id,
                created_at=job.created_at,
                normalized_url=source.normalized_url if source is not None else None,
                final_url=cast(str | None, ingestion_metadata.get("final_url")),
                selected_metadata=cast(
                    dict[str, str | None] | None, ingestion_metadata.get("selected_metadata")
                ),
                lifecycle_state=item.status if item is not None else None,
                warnings=cast(list[str], ingestion_metadata.get("warnings", [])),
                correlation_id=job.correlation_id,
            )
        except IntegrityError as error:
            session.rollback()
            raise HTTPException(
                status_code=409, detail="ingestion submission conflicts with an existing record"
            ) from error

    @app.post(
        "/api/v1/ingestion/upload",
        response_model=UploadIngestionRead,
        status_code=status.HTTP_201_CREATED,
    )
    async def ingest_upload(
        request: Request,
        response: Response,
        file: Annotated[UploadFile, File()],
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
        source_id: Annotated[uuid.UUID | None, Form()] = None,
        idempotency_key: Annotated[str | None, Form(min_length=8, max_length=255)] = None,
        notes: Annotated[str | None, Form(max_length=2_000)] = None,
        original_source_url: Annotated[str | None, Form(max_length=2_048)] = None,
        rights_declaration: Annotated[str | None, Form(max_length=100)] = None,
        rights_notes: Annotated[str | None, Form(max_length=2_000)] = None,
        attribution: Annotated[str | None, Form(max_length=2_000)] = None,
    ) -> UploadIngestionRead:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR)
        key = idempotency_key or str(uuid.uuid4())
        existing = session.scalar(select(IngestionJob).where(IngestionJob.idempotency_key == key))

        async def chunks() -> AsyncIterator[bytes]:
            while True:
                chunk = await file.read(get_settings().upload_chunk_bytes)
                if not chunk:
                    break
                yield chunk

        try:
            job = await submit_upload(
                session,
                actor.id,
                chunks(),
                file.filename,
                file.content_type,
                key,
                source_id,
                notes,
                original_source_url,
                rights_declaration,
                rights_notes,
                attribution,
                request.headers.get("X-Correlation-ID"),
            )
        finally:
            await file.close()
        session.refresh(job)
        asset = (
            session.get(MediaAsset, job.result_asset_id)
            if job.result_asset_id is not None
            else None
        )
        item = (
            session.get(ContentItem, job.result_content_id)
            if job.result_content_id is not None
            else None
        )
        if existing is not None:
            response.status_code = status.HTTP_200_OK
        duplicate = (
            "FILE_HASH_DUPLICATE"
            if asset is not None
            and job.result_content_id is not None
            and existing is None
            and asset.source_id != job.source_id
            else None
        )
        return UploadIngestionRead(
            id=job.id,
            status=job.status.value,
            content_id=job.result_content_id,
            asset_id=job.result_asset_id,
            duplicate_outcome=duplicate,
            original_filename=asset.display_filename if asset is not None else file.filename,
            detected_media_type=asset.detected_media_type if asset is not None else None,
            file_size_bytes=asset.file_size_bytes if asset is not None else None,
            sha256=asset.checksum if asset is not None else None,
            lifecycle_state=item.status if item is not None else None,
            correlation_id=job.correlation_id,
        )

    @app.post(
        "/api/v1/production/projects",
        response_model=ProductionProjectRead,
        status_code=status.HTTP_201_CREATED,
    )
    def create_production_project(
        payload: ProductionProjectCreate,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> ProductionProject:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR)
        brand = brand_for_actor(session, actor.id, actor.roles, payload.brand_id)
        return create_project(
            session,
            actor.id,
            payload.source_url,
            payload.source_title,
            payload.source_channel,
            brand_id=brand.id,
        )

    @app.get("/api/v1/production/projects", response_model=list[ProductionProjectRead])
    def list_production_projects(
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
        brand_id: uuid.UUID | None = None,
    ) -> list[ProductionProject]:
        require_role(
            actor,
            RoleName.OWNER,
            RoleName.ADMIN,
            RoleName.EDITOR,
            RoleName.REVIEWER,
            RoleName.ANALYST,
            RoleName.VIEWER,
        )
        brand = brand_for_actor(session, actor.id, actor.roles, brand_id)
        return list(
            session.scalars(
                select(ProductionProject)
                .where(ProductionProject.brand_id == brand.id)
                .order_by(ProductionProject.created_at.desc(), ProductionProject.id.desc())
                .limit(100)
            )
        )

    @app.get("/api/v1/production/projects/{project_id}", response_model=ProductionProjectRead)
    def get_production_project(
        project_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> ProductionProject:
        require_role(
            actor,
            RoleName.OWNER,
            RoleName.ADMIN,
            RoleName.EDITOR,
            RoleName.REVIEWER,
            RoleName.ANALYST,
            RoleName.VIEWER,
        )
        project = session.get(ProductionProject, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="production project not found")
        require_record_brand(session, actor, project)
        return project

    @app.get(
        "/api/v1/production/projects/{project_id}/sources",
        response_model=list[ProductionSourceRead],
    )
    def list_production_sources(
        project_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> list[ProductionSource]:
        require_role(
            actor,
            RoleName.OWNER,
            RoleName.ADMIN,
            RoleName.EDITOR,
            RoleName.REVIEWER,
            RoleName.ANALYST,
            RoleName.VIEWER,
        )
        project = session.get(ProductionProject, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="production project not found")
        require_record_brand(session, actor, project)
        return list(
            session.scalars(
                select(ProductionSource)
                .where(ProductionSource.project_id == project_id)
                .order_by(ProductionSource.quality_score.desc(), ProductionSource.created_at)
            )
        )

    @app.post(
        "/api/v1/production/projects/{project_id}/source/accept",
        response_model=ProductionProjectRead,
    )
    def accept_production_source(
        project_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> ProductionProject:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR, RoleName.REVIEWER)
        project = session.get(ProductionProject, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="production project not found")
        require_record_brand(session, actor, project)
        return accept_source(session, actor.id, project)

    @app.post(
        "/api/v1/production/projects/{project_id}/source/reject",
        response_model=ProductionProjectRead,
    )
    def reject_production_source(
        project_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> ProductionProject:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR, RoleName.REVIEWER)
        project = session.get(ProductionProject, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="production project not found")
        require_record_brand(session, actor, project)
        return reject_source(session, actor.id, project)

    @app.post(
        "/api/v1/production/projects/{project_id}/source/select",
        response_model=ProductionProjectRead,
    )
    def choose_production_source(
        project_id: uuid.UUID,
        payload: SourceChoiceRequest,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> ProductionProject:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR, RoleName.REVIEWER)
        project = session.get(ProductionProject, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="production project not found")
        require_record_brand(session, actor, project)
        return choose_source(
            session, actor.id, project, payload.source_id, payload.expected_version
        )

    @app.post(
        "/api/v1/production/projects/{project_id}/download", response_model=ProductionProjectRead
    )
    def download_production_project(
        project_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
        storage: Annotated[LocalFilesystemStorage, Depends(get_production_storage)],
    ) -> ProductionProject:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR)
        project = session.get(ProductionProject, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="production project not found")
        return download_project(session, actor.id, project, storage)

    @app.post(
        "/api/v1/production/projects/{project_id}/analysis",
        response_model=VideoAnalysisRead,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def start_project_analysis(
        project_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
        payload: AnalysisStartRequest | None = None,
    ) -> VideoAnalysis:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR)
        project = session.get(ProductionProject, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="production project not found")
        analysis = request_analysis(
            session,
            actor.id,
            project,
            analysis_version=payload.analysis_version if payload else None,
        )
        if get_settings().environment is not Environment.TEST:
            from app.worker import run_video_analysis

            run_video_analysis.delay(str(project_id), analysis_version=analysis.analysis_version)
        return analysis

    @app.post(
        "/api/v1/production/projects/{project_id}/analysis/rerun",
        response_model=VideoAnalysisRead,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def rerun_project_analysis(
        project_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
        payload: AnalysisStartRequest | None = None,
    ) -> VideoAnalysis:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN)
        project = session.get(ProductionProject, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="production project not found")
        analysis = request_analysis(
            session,
            actor.id,
            project,
            rerun=True,
            analysis_version=payload.analysis_version if payload else None,
        )
        if get_settings().environment is not Environment.TEST:
            from app.worker import run_video_analysis

            run_video_analysis.delay(
                str(project_id), rerun=True, analysis_version=analysis.analysis_version
            )
        return analysis

    @app.post("/api/v1/analysis/{analysis_id}/cancel", response_model=VideoAnalysisRead)
    def cancel_project_analysis(
        analysis_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> VideoAnalysis:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR)
        analysis = session.get(VideoAnalysis, analysis_id)
        if analysis is None:
            raise HTTPException(status_code=404, detail="analysis not found")
        return cancel_analysis(session, actor.id, analysis)

    @app.get(
        "/api/v1/production/projects/{project_id}/analysis",
        response_model=list[VideoAnalysisRead],
    )
    def list_project_analysis(
        project_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> list[VideoAnalysis]:
        require_role(
            actor,
            RoleName.OWNER,
            RoleName.ADMIN,
            RoleName.EDITOR,
            RoleName.REVIEWER,
            RoleName.ANALYST,
            RoleName.VIEWER,
        )
        if session.get(ProductionProject, project_id) is None:
            raise HTTPException(status_code=404, detail="production project not found")
        return list(
            session.scalars(
                select(VideoAnalysis)
                .where(VideoAnalysis.project_id == project_id)
                .order_by(VideoAnalysis.created_at.desc())
            )
        )

    @app.get("/api/v1/analysis/{analysis_id}", response_model=VideoAnalysisRead)
    def get_analysis(
        analysis_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> VideoAnalysis:
        require_role(
            actor,
            RoleName.OWNER,
            RoleName.ADMIN,
            RoleName.EDITOR,
            RoleName.REVIEWER,
            RoleName.ANALYST,
            RoleName.VIEWER,
        )
        analysis = session.get(VideoAnalysis, analysis_id)
        if analysis is None:
            raise HTTPException(status_code=404, detail="analysis not found")
        return analysis

    @app.get("/api/v1/analysis/{analysis_id}/timeline", response_model=list[AnalysisSegmentRead])
    def get_analysis_timeline(
        analysis_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[AnalysisSegment]:
        require_role(
            actor,
            RoleName.OWNER,
            RoleName.ADMIN,
            RoleName.EDITOR,
            RoleName.REVIEWER,
            RoleName.ANALYST,
            RoleName.VIEWER,
        )
        if session.get(VideoAnalysis, analysis_id) is None:
            raise HTTPException(status_code=404, detail="analysis not found")
        return list(
            session.scalars(
                select(AnalysisSegment)
                .where(AnalysisSegment.analysis_id == analysis_id)
                .order_by(AnalysisSegment.start_time, AnalysisSegment.created_at)
                .offset(offset)
                .limit(limit)
            )
        )

    @app.get(
        "/api/v1/analysis/{analysis_id}/transcript", response_model=list[TranscriptSegmentRead]
    )
    def get_analysis_transcript(
        analysis_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[TranscriptSegment]:
        require_role(
            actor,
            RoleName.OWNER,
            RoleName.ADMIN,
            RoleName.EDITOR,
            RoleName.REVIEWER,
            RoleName.ANALYST,
            RoleName.VIEWER,
        )
        if session.get(VideoAnalysis, analysis_id) is None:
            raise HTTPException(status_code=404, detail="analysis not found")
        return list(
            session.scalars(
                select(TranscriptSegment)
                .where(TranscriptSegment.analysis_id == analysis_id)
                .order_by(TranscriptSegment.start_time, TranscriptSegment.created_at)
                .offset(offset)
                .limit(limit)
            )
        )

    @app.get("/api/v1/analysis/{analysis_id}/events", response_model=list[AnalysisEventRead])
    def get_analysis_events(
        analysis_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[AnalysisEvent]:
        require_role(
            actor,
            RoleName.OWNER,
            RoleName.ADMIN,
            RoleName.EDITOR,
            RoleName.REVIEWER,
            RoleName.ANALYST,
            RoleName.VIEWER,
        )
        if session.get(VideoAnalysis, analysis_id) is None:
            raise HTTPException(status_code=404, detail="analysis not found")
        return list(
            session.scalars(
                select(AnalysisEvent)
                .where(AnalysisEvent.analysis_id == analysis_id)
                .order_by(AnalysisEvent.timestamp, AnalysisEvent.created_at)
                .offset(offset)
                .limit(limit)
            )
        )

    @app.post(
        "/api/v1/production/projects/{project_id}/opportunities",
        response_model=OpportunityGenerationRunRead,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def start_opportunity_generation(
        project_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> OpportunityGenerationRun:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR)
        if session.get(ProductionProject, project_id) is None:
            raise HTTPException(status_code=404, detail="production project not found")
        analysis = session.scalar(
            select(VideoAnalysis)
            .where(
                VideoAnalysis.project_id == project_id,
                VideoAnalysis.status == "COMPLETED",
            )
            .order_by(VideoAnalysis.completed_at.desc())
        )
        if analysis is None:
            raise ProductionError("ANALYSIS_NOT_READY", "complete analysis is required")
        run = request_opportunity_generation(session, actor.id, analysis)
        if get_settings().environment is not Environment.TEST:
            from app.worker import generate_clip_opportunities

            generate_clip_opportunities.delay(str(analysis.id))
        return run

    @app.post(
        "/api/v1/production/projects/{project_id}/opportunities/regenerate",
        response_model=OpportunityGenerationRunRead,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def regenerate_opportunities(
        project_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> OpportunityGenerationRun:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR)
        analysis = session.scalar(
            select(VideoAnalysis)
            .where(
                VideoAnalysis.project_id == project_id,
                VideoAnalysis.status == "COMPLETED",
            )
            .order_by(VideoAnalysis.completed_at.desc())
        )
        if analysis is None:
            raise ProductionError("ANALYSIS_NOT_READY", "complete analysis is required")
        run = request_opportunity_generation(session, actor.id, analysis, rerun=True)
        if get_settings().environment is not Environment.TEST:
            from app.worker import generate_clip_opportunities

            generate_clip_opportunities.delay(str(analysis.id), rerun=True)
        return run

    @app.get(
        "/api/v1/production/projects/{project_id}/opportunity-generation",
        response_model=list[OpportunityGenerationRunRead],
    )
    def list_opportunity_generation_runs(
        project_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> list[OpportunityGenerationRun]:
        require_role(
            actor,
            RoleName.OWNER,
            RoleName.ADMIN,
            RoleName.EDITOR,
            RoleName.REVIEWER,
            RoleName.ANALYST,
            RoleName.VIEWER,
        )
        if session.get(ProductionProject, project_id) is None:
            raise HTTPException(status_code=404, detail="production project not found")
        return list(
            session.scalars(
                select(OpportunityGenerationRun)
                .where(OpportunityGenerationRun.project_id == project_id)
                .order_by(OpportunityGenerationRun.created_at.desc())
            )
        )

    @app.get(
        "/api/v1/production/projects/{project_id}/opportunities",
        response_model=list[ClipOpportunityRead],
    )
    def list_opportunities(
        project_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> list[ClipOpportunity]:
        require_role(
            actor,
            RoleName.OWNER,
            RoleName.ADMIN,
            RoleName.EDITOR,
            RoleName.REVIEWER,
            RoleName.ANALYST,
            RoleName.VIEWER,
        )
        run = session.scalar(
            select(OpportunityGenerationRun)
            .where(OpportunityGenerationRun.project_id == project_id)
            .order_by(OpportunityGenerationRun.generation_version.desc())
        )
        if run is None:
            return []
        return list(
            session.scalars(
                select(ClipOpportunity)
                .where(
                    ClipOpportunity.project_id == project_id,
                    ClipOpportunity.generation_version == run.generation_version,
                )
                .order_by(ClipOpportunity.overall_score.desc(), ClipOpportunity.start_time)
            )
        )

    @app.get("/api/v1/opportunities/{opportunity_id}", response_model=ClipOpportunityRead)
    def get_opportunity(
        opportunity_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> ClipOpportunity:
        require_role(
            actor,
            RoleName.OWNER,
            RoleName.ADMIN,
            RoleName.EDITOR,
            RoleName.REVIEWER,
            RoleName.ANALYST,
            RoleName.VIEWER,
        )
        opportunity = session.get(ClipOpportunity, opportunity_id)
        if opportunity is None:
            raise HTTPException(status_code=404, detail="clip opportunity not found")
        return opportunity

    @app.get(
        "/api/v1/opportunities/{opportunity_id}/reasons",
        response_model=list[OpportunityReasonRead],
    )
    def get_opportunity_reasons(
        opportunity_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> list[OpportunityReason]:
        require_role(
            actor,
            RoleName.OWNER,
            RoleName.ADMIN,
            RoleName.EDITOR,
            RoleName.REVIEWER,
            RoleName.ANALYST,
            RoleName.VIEWER,
        )
        if session.get(ClipOpportunity, opportunity_id) is None:
            raise HTTPException(status_code=404, detail="clip opportunity not found")
        return list(
            session.scalars(
                select(OpportunityReason)
                .where(OpportunityReason.opportunity_id == opportunity_id)
                .order_by(OpportunityReason.weight.desc(), OpportunityReason.reason_type)
            )
        )

    @app.get(
        "/api/v1/opportunities/{opportunity_id}/versions",
        response_model=list[ClipOpportunityVersionRead],
    )
    def get_opportunity_versions(
        opportunity_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> list[ClipOpportunityVersion]:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR, RoleName.REVIEWER)
        if session.get(ClipOpportunity, opportunity_id) is None:
            raise HTTPException(status_code=404, detail="clip opportunity not found")
        return list(
            session.scalars(
                select(ClipOpportunityVersion)
                .where(ClipOpportunityVersion.opportunity_id == opportunity_id)
                .order_by(ClipOpportunityVersion.version)
            )
        )

    @app.post("/api/v1/opportunities/{opportunity_id}/approve", response_model=ClipOpportunityRead)
    def approve_opportunity(
        opportunity_id: uuid.UUID,
        payload: OpportunityDecisionRequest,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
        storage: Annotated[LocalFilesystemStorage, Depends(get_production_storage)],
    ) -> ClipOpportunity:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR, RoleName.REVIEWER)
        opportunity = session.get(ClipOpportunity, opportunity_id)
        if opportunity is None:
            raise HTTPException(status_code=404, detail="clip opportunity not found")
        decision = decide_opportunity(
            session, actor.id, opportunity, True, payload.expected_version, payload.reason
        )
        generate_approved_opportunity(session, actor.id, decision, storage)
        return decision

    @app.post("/api/v1/opportunities/{opportunity_id}/reject", response_model=ClipOpportunityRead)
    def reject_opportunity(
        opportunity_id: uuid.UUID,
        payload: OpportunityDecisionRequest,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> ClipOpportunity:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR, RoleName.REVIEWER)
        opportunity = session.get(ClipOpportunity, opportunity_id)
        if opportunity is None:
            raise HTTPException(status_code=404, detail="clip opportunity not found")
        return decide_opportunity(
            session, actor.id, opportunity, False, payload.expected_version, payload.reason
        )

    @app.post(
        "/api/v1/production/projects/{project_id}/generate-clips",
        response_model=list[ProductionClipRead],
    )
    def generate_production_clips(
        project_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
        storage: Annotated[LocalFilesystemStorage, Depends(get_production_storage)],
    ) -> list[ProductionClip]:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR)
        project = session.get(ProductionProject, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="production project not found")
        return generate_clips(session, actor.id, project, storage)

    @app.get(
        "/api/v1/production/projects/{project_id}/clips", response_model=list[ProductionClipRead]
    )
    def list_production_clips(
        project_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> list[ProductionClip]:
        require_role(
            actor,
            RoleName.OWNER,
            RoleName.ADMIN,
            RoleName.EDITOR,
            RoleName.REVIEWER,
            RoleName.ANALYST,
            RoleName.VIEWER,
        )
        if session.get(ProductionProject, project_id) is None:
            raise HTTPException(status_code=404, detail="production project not found")
        return list(
            session.scalars(
                select(ProductionClip)
                .where(ProductionClip.project_id == project_id)
                .order_by(ProductionClip.clip_number)
            )
        )

    @app.post(
        "/api/v1/production/clips/{clip_id}/content-packages",
        response_model=ContentPackageRead,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def generate_content_package_for_clip(
        clip_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> ContentPackage:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR)
        clip = session.get(ProductionClip, clip_id)
        if clip is None:
            raise HTTPException(status_code=404, detail="production clip not found")
        require_record_brand(session, actor, clip)
        package = request_content_package_generation(session, actor.id, clip)
        if get_settings().environment is not Environment.TEST:
            from app.worker import generate_content_package

            generate_content_package.delay(str(clip.id))
        return package

    @app.post(
        "/api/v1/production/clips/{clip_id}/content-packages/regenerate",
        response_model=ContentPackageRead,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def regenerate_content_package_for_clip(
        clip_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> ContentPackage:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR)
        clip = session.get(ProductionClip, clip_id)
        if clip is None:
            raise HTTPException(status_code=404, detail="production clip not found")
        require_record_brand(session, actor, clip)
        package = request_content_package_generation(session, actor.id, clip, rerun=True)
        if get_settings().environment is not Environment.TEST:
            from app.worker import generate_content_package

            generate_content_package.delay(str(clip.id), rerun=True)
        return package

    @app.get(
        "/api/v1/production/clips/{clip_id}/content-packages",
        response_model=list[ContentPackageRead],
    )
    def list_content_packages_for_clip(
        clip_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> list[ContentPackage]:
        require_role(
            actor,
            RoleName.OWNER,
            RoleName.ADMIN,
            RoleName.EDITOR,
            RoleName.REVIEWER,
            RoleName.VIEWER,
        )
        clip = session.get(ProductionClip, clip_id)
        if clip is None:
            raise HTTPException(status_code=404, detail="production clip not found")
        require_record_brand(session, actor, clip)
        return list(
            session.scalars(
                select(ContentPackage)
                .where(ContentPackage.clip_id == clip_id)
                .order_by(ContentPackage.generation_version.desc())
            )
        )

    @app.get("/api/v1/content-packages/{package_id}", response_model=ContentPackageRead)
    def get_content_package(
        package_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> ContentPackage:
        require_role(
            actor,
            RoleName.OWNER,
            RoleName.ADMIN,
            RoleName.EDITOR,
            RoleName.REVIEWER,
            RoleName.VIEWER,
        )
        package = session.get(ContentPackage, package_id)
        if package is None:
            raise HTTPException(status_code=404, detail="content package not found")
        require_record_brand(session, actor, package)
        return package

    @app.get(
        "/api/v1/content-packages/{package_id}/versions",
        response_model=list[ContentPackageVersionRead],
    )
    def list_content_package_versions(
        package_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> list[ContentPackageVersion]:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR, RoleName.REVIEWER)
        package = session.get(ContentPackage, package_id)
        if package is None:
            raise HTTPException(status_code=404, detail="content package not found")
        require_record_brand(session, actor, package)
        return list(
            session.scalars(
                select(ContentPackageVersion)
                .where(ContentPackageVersion.content_package_id == package_id)
                .order_by(ContentPackageVersion.version)
            )
        )

    @app.patch("/api/v1/content-packages/{package_id}", response_model=ContentPackageRead)
    def edit_package(
        package_id: uuid.UUID,
        payload: ContentPackageEditRequest,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> ContentPackage:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR, RoleName.REVIEWER)
        package = session.get(ContentPackage, package_id)
        if package is None:
            raise HTTPException(status_code=404, detail="content package not found")
        require_record_brand(session, actor, package)
        return edit_content_package(
            session, actor.id, package, payload.expected_version, payload.fields_json
        )

    @app.post("/api/v1/content-packages/{package_id}/approve", response_model=ContentPackageRead)
    def approve_content_package(
        package_id: uuid.UUID,
        payload: ContentPackageDecisionRequest,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> ContentPackage:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR, RoleName.REVIEWER)
        package = session.get(ContentPackage, package_id)
        if package is None:
            raise HTTPException(status_code=404, detail="content package not found")
        require_record_brand(session, actor, package)
        return decide_content_package(
            session, actor.id, package, payload.expected_version, True, payload.reason
        )

    @app.post("/api/v1/content-packages/{package_id}/reject", response_model=ContentPackageRead)
    def reject_content_package(
        package_id: uuid.UUID,
        payload: ContentPackageDecisionRequest,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> ContentPackage:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR, RoleName.REVIEWER)
        package = session.get(ContentPackage, package_id)
        if package is None:
            raise HTTPException(status_code=404, detail="content package not found")
        require_record_brand(session, actor, package)
        return decide_content_package(
            session, actor.id, package, payload.expected_version, False, payload.reason
        )

    @app.post(
        "/api/v1/production/projects/{project_id}/producer/recommendations",
        response_model=list[ProducerRecommendationRead],
        status_code=status.HTTP_201_CREATED,
    )
    def generate_producer_recommendations_for_project(
        project_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> list[ProducerRecommendation]:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR, RoleName.REVIEWER)
        project = session.get(ProductionProject, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="production project not found")
        require_record_brand(session, actor, project)
        return generate_project_recommendations(session, actor.id, project)

    @app.get(
        "/api/v1/production/projects/{project_id}/producer/recommendations",
        response_model=list[ProducerRecommendationRead],
    )
    def list_producer_recommendations_for_project(
        project_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> list[ProducerRecommendation]:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR, RoleName.REVIEWER, RoleName.VIEWER)
        project = session.get(ProductionProject, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="production project not found")
        require_record_brand(session, actor, project)
        return list(session.scalars(select(ProducerRecommendation).where(ProducerRecommendation.project_id == project.id).order_by(ProducerRecommendation.created_at.desc())))

    @app.post("/api/v1/producer/recommendations/{recommendation_id}/approve", response_model=ProducerRecommendationRead)
    def approve_producer_recommendation(
        recommendation_id: uuid.UUID,
        payload: ProducerDecisionRequest,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> ProducerRecommendation:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR, RoleName.REVIEWER)
        recommendation = session.get(ProducerRecommendation, recommendation_id)
        if recommendation is None:
            raise HTTPException(status_code=404, detail="producer recommendation not found")
        require_record_brand(session, actor, recommendation)
        return decide_recommendation(session, actor.id, recommendation, payload.expected_version, True, payload.operator_edit_json, payload.reason)

    @app.post("/api/v1/producer/recommendations/{recommendation_id}/reject", response_model=ProducerRecommendationRead)
    def reject_producer_recommendation(
        recommendation_id: uuid.UUID,
        payload: ProducerDecisionRequest,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> ProducerRecommendation:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR, RoleName.REVIEWER)
        recommendation = session.get(ProducerRecommendation, recommendation_id)
        if recommendation is None:
            raise HTTPException(status_code=404, detail="producer recommendation not found")
        require_record_brand(session, actor, recommendation)
        return decide_recommendation(session, actor.id, recommendation, payload.expected_version, False, payload.operator_edit_json, payload.reason)

    @app.post(
        "/api/v1/production/clips/{clip_id}/producer/quality-report",
        response_model=ClipQualityReportRead,
        status_code=status.HTTP_201_CREATED,
    )
    def generate_quality_report_for_clip(
        clip_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> ClipQualityReport:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR, RoleName.REVIEWER)
        clip = session.get(ProductionClip, clip_id)
        if clip is None:
            raise HTTPException(status_code=404, detail="production clip not found")
        require_record_brand(session, actor, clip)
        report = generate_clip_quality_report(session, actor.id, clip)
        generate_clip_recommendations(session, actor.id, clip)
        return report

    @app.get("/api/v1/production/clips/{clip_id}/producer/quality-reports", response_model=list[ClipQualityReportRead])
    def list_quality_reports_for_clip(
        clip_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> list[ClipQualityReport]:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR, RoleName.REVIEWER, RoleName.VIEWER)
        clip = session.get(ProductionClip, clip_id)
        if clip is None:
            raise HTTPException(status_code=404, detail="production clip not found")
        require_record_brand(session, actor, clip)
        return list(session.scalars(select(ClipQualityReport).where(ClipQualityReport.clip_id == clip.id).order_by(ClipQualityReport.report_version.desc())))

    @app.post("/api/v1/production/clips/{clip_id}/approve", response_model=ProductionClipRead)
    def approve_production_clip(
        clip_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> ProductionClip:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR, RoleName.REVIEWER)
        clip = session.get(ProductionClip, clip_id)
        if clip is None:
            raise HTTPException(status_code=404, detail="production clip not found")
        require_record_brand(session, actor, clip)
        return decide_clip(session, actor.id, clip, True)

    @app.post("/api/v1/production/clips/{clip_id}/reject", response_model=ProductionClipRead)
    def reject_production_clip(
        clip_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> ProductionClip:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR, RoleName.REVIEWER)
        clip = session.get(ProductionClip, clip_id)
        if clip is None:
            raise HTTPException(status_code=404, detail="production clip not found")
        require_record_brand(session, actor, clip)
        return decide_clip(session, actor.id, clip, False)

    @app.post("/api/v1/production/clips/{clip_id}/publish", response_model=ProductionQueueRead)
    def publish_production_clip(
        clip_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> PostingQueueItem:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN)
        queue = session.scalar(select(PostingQueueItem).where(PostingQueueItem.clip_id == clip_id))
        if queue is None:
            raise HTTPException(status_code=409, detail="approved clip is not in the posting queue")
        raise ProductionError(
            "YOUTUBE_NOT_CONFIGURED", "YouTube OAuth publishing is not configured"
        )

    @app.post(
        "/api/v1/publishing/destination-accounts/{account_id}/verify",
        response_model=DestinationConnectionRead,
    )
    def verify_publishing_destination(
        account_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> PublishingAccountConnection:
        require_trusted_https_feature()
        require_role(actor, RoleName.OWNER, RoleName.ADMIN)
        account = session.get(DestinationAccount, account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="destination account not found")
        require_record_brand(session, actor, account)
        return verify_destination_connection(session, actor.id, account)

    @app.post("/api/v1/publishing/tiktok/oauth/start")
    def start_tiktok_oauth(
        payload: TikTokOAuthStart,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> dict[str, object]:
        """Create an expiring, brand-bound Login Kit authorization URL."""
        require_trusted_https_feature()
        require_role(actor, RoleName.OWNER, RoleName.ADMIN)
        account = session.get(DestinationAccount, payload.destination_account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="destination account not found")
        require_record_brand(session, actor, account)
        if account.provider.upper() != "TIKTOK":
            raise HTTPException(status_code=409, detail="TikTok destination account required")
        state, raw_state = create_oauth_state(session, account, scopes=payload.requested_scopes)
        return {
            "authorization_url": TikTokPublishingProvider().authorization_url(
                raw_state, state.requested_scopes, oauth_code_challenge(session, state)
            ),
            "expires_at": state.expires_at,
            "destination_account_id": str(account.id),
        }

    @app.get("/api/v1/oauth/tiktok/callback")
    def complete_tiktok_oauth(
        state: str | None = None,
        code: str | None = None,
        error: str | None = None,
        session: Annotated[Session, Depends(get_session)] = None,  # type: ignore[assignment]
    ) -> JSONResponse:
        """Public state-protected callback.  Tokens never enter persistence or logs."""
        if error:
            if state:
                try:
                    oauth_state = consume_oauth_state(session, state)
                    consume_oauth_verifier(oauth_state)
                except PublishingError:
                    pass
            return JSONResponse(status_code=400, content={"status": "denied", "message": "TikTok authorization was not completed."})
        if not state or not code:
            return JSONResponse(status_code=400, content={"status": "invalid", "message": "TikTok callback is missing authorization data."})
        try:
            oauth_state = consume_oauth_state(session, state)
            verifier = consume_oauth_verifier(oauth_state)
            token_set = TikTokPublishingProvider().exchange_code(code, verifier)
            if not set(oauth_state.requested_scopes).issubset(token_set.scopes):
                raise PublishingError("TIKTOK_REQUIRED_SCOPE_MISSING", "TikTok did not grant every required scope")
        except PublishingError as exc:
            return JSONResponse(status_code=400, content={"status": "failed", "code": exc.code, "message": exc.message})
        account = session.get(DestinationAccount, oauth_state.destination_account_id)
        if account is None or account.brand_id != oauth_state.brand_id:
            return JSONResponse(status_code=400, content={"status": "failed", "code": "TIKTOK_OAUTH_DESTINATION_INVALID", "message": "TikTok authorization destination is unavailable."})
        connection = session.scalar(select(PublishingAccountConnection).where(PublishingAccountConnection.destination_account_id == oauth_state.destination_account_id))
        if connection is None:
            connection = PublishingAccountConnection(destination_account_id=oauth_state.destination_account_id)
        try:
            store = credential_store(get_settings())
            credential_reference = store.create(token_set.payload(), namespace="tiktok")
            account.credential_reference_id = credential_reference
            identity, channel_url = TikTokPublishingProvider().verify_connection(account)
        except PublishingError as exc:
            if "credential_reference" in locals():
                store.delete(credential_reference)
            connection.connection_state = "DEGRADED"
            connection.last_error_category, connection.last_error_summary = exc.code, exc.message
            connection.checked_at = datetime.now(UTC).isoformat()
            session.add(connection)
            session.commit()
            return JSONResponse(status_code=400, content={"status": "failed", "code": exc.code, "message": exc.message})
        connection.connection_state = "CONNECTED"
        connection.provider_account_id, connection.provider_channel_url = identity, channel_url
        connection.granted_scopes = sorted(token_set.scopes)
        connection.credential_expires_at = str(token_set.payload().get("expires_at") or "") or None
        connection.checked_at = datetime.now(UTC).isoformat()
        connection.last_error_category, connection.last_error_summary = None, None
        session.add(connection)
        session.add(account)
        session.add(AuditEvent(entity_type="destination_account", entity_id=oauth_state.destination_account_id, brand_id=oauth_state.brand_id, event_name="tiktok.oauth.connected", payload={"scopes": sorted(token_set.scopes)}))
        session.commit()
        return JSONResponse(status_code=200, content={"status": "connected", "destination_account_id": str(oauth_state.destination_account_id), "granted_scopes": sorted(token_set.scopes), "message": "TikTok account connected securely."})

    @app.get("/api/v1/publishing/tiktok/destination-accounts/{account_id}/status", response_model=TikTokConnectionStatusRead)
    def tiktok_connection_status(
        account_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> TikTokConnectionStatusRead:
        account = session.get(DestinationAccount, account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="destination account not found")
        require_record_brand(session, actor, account)
        connection = session.scalar(select(PublishingAccountConnection).where(PublishingAccountConnection.destination_account_id == account.id))
        if connection is None:
            raise HTTPException(status_code=404, detail="TikTok connection not found")
        return TikTokConnectionStatusRead.model_validate({**{field: getattr(connection, field) for field in DestinationConnectionRead.model_fields}, "granted_scopes": connection.granted_scopes, "application_review_state": get_settings().tiktok_application_review_state})

    @app.post("/api/v1/publishing/tiktok/destination-accounts/{account_id}/capabilities", response_model=TikTokCapabilityRead)
    def query_tiktok_capabilities(
        account_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> TikTokCreatorCapability:
        require_trusted_https_feature()
        require_role(actor, RoleName.OWNER, RoleName.ADMIN)
        account = session.get(DestinationAccount, account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="destination account not found")
        require_record_brand(session, actor, account)
        if account.provider.upper() != "TIKTOK":
            raise HTTPException(status_code=409, detail="TikTok destination account required")
        return persist_capabilities(session, account, TikTokPublishingProvider().creator_info(account))

    @app.post(
        "/api/v1/publishing/tiktok/destination-accounts/{account_id}/refresh",
        response_model=DestinationConnectionRead,
    )
    def refresh_tiktok_connection_route(
        account_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> PublishingAccountConnection:
        require_trusted_https_feature()
        require_role(actor, RoleName.OWNER, RoleName.ADMIN)
        account = session.get(DestinationAccount, account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="destination account not found")
        require_record_brand(session, actor, account)
        return refresh_tiktok_connection(session, actor.id, account.id)

    @app.delete(
        "/api/v1/publishing/tiktok/destination-accounts/{account_id}",
        response_model=DestinationConnectionRead,
    )
    def disconnect_tiktok_connection_route(
        account_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> PublishingAccountConnection:
        require_trusted_https_feature()
        require_role(actor, RoleName.OWNER, RoleName.ADMIN)
        account = session.get(DestinationAccount, account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="destination account not found")
        require_record_brand(session, actor, account)
        return disconnect_tiktok_connection(session, actor.id, account)

    def _create_tiktok_request(payload: TikTokPublishRequestCreate, mode: str, actor: Actor, session: Session) -> PublishRequest:
        require_trusted_https_feature()
        require_role(actor, RoleName.OWNER, RoleName.ADMIN)
        clip, package, destination = session.get(ProductionClip, payload.clip_id), session.get(ContentPackage, payload.content_package_id), session.get(DestinationAccount, payload.destination_account_id)
        if clip is None or package is None or destination is None:
            raise HTTPException(status_code=404, detail="clip, content package, or destination account not found")
        require_record_brand(session, actor, clip)
        require_record_brand(session, actor, package)
        require_record_brand(session, actor, destination)
        return request_tiktok_publish(session, actor.id, clip, package, destination, payload.idempotency_key, mode, payload.privacy_level)

    @app.post("/api/v1/publishing/tiktok/requests/draft", response_model=PublishRequestRead, status_code=status.HTTP_201_CREATED)
    def create_tiktok_draft_request(payload: TikTokPublishRequestCreate, actor: Annotated[Actor, Depends(development_actor)], session: Annotated[Session, Depends(get_session)]) -> PublishRequest:
        return _create_tiktok_request(payload, "DRAFT_UPLOAD", actor, session)

    @app.post("/api/v1/publishing/tiktok/requests/direct", response_model=PublishRequestRead, status_code=status.HTTP_201_CREATED)
    def create_tiktok_direct_request(payload: TikTokPublishRequestCreate, actor: Annotated[Actor, Depends(development_actor)], session: Annotated[Session, Depends(get_session)]) -> PublishRequest:
        return _create_tiktok_request(payload, "DIRECT_POST", actor, session)

    @app.post("/api/v1/publishing/tiktok/requests/{request_id}/confirm", response_model=PublishRequestRead)
    def confirm_tiktok_request(request_id: uuid.UUID, actor: Annotated[Actor, Depends(development_actor)], session: Annotated[Session, Depends(get_session)]) -> PublishRequest:
        request = session.get(PublishRequest, request_id)
        if request is None:
            raise HTTPException(status_code=404, detail="publishing request not found")
        require_record_brand(session, actor, request)
        if request.provider_mode not in {"DRAFT_UPLOAD", "DIRECT_POST"}:
            raise HTTPException(status_code=409, detail="TikTok request required")
        confirmed = confirm_publish(session, actor.id, request)
        if confirmed.status == "QUEUED":
            from app.worker import celery_app
            celery_app.send_task("viralforge.execute_tiktok_publish_request", args=[str(confirmed.id)])
        return confirmed

    @app.post("/api/v1/publishing/tiktok/requests/{request_id}/refresh", response_model=PublishRequestRead)
    def refresh_tiktok_request(request_id: uuid.UUID, actor: Annotated[Actor, Depends(development_actor)], session: Annotated[Session, Depends(get_session)]) -> PublishRequest:
        request = session.get(PublishRequest, request_id)
        if request is None:
            raise HTTPException(status_code=404, detail="publishing request not found")
        require_record_brand(session, actor, request)
        return refresh_tiktok_status(session, request.id)

    @app.post("/api/v1/publishing/tiktok/requests/{request_id}/draft-completion/{outcome}", response_model=PublishRequestRead)
    def complete_tiktok_draft_request(request_id: uuid.UUID, outcome: str, post_url: str | None = None, actor: Annotated[Actor, Depends(development_actor)] = None, session: Annotated[Session, Depends(get_session)] = None) -> PublishRequest:  # type: ignore[assignment]
        request = session.get(PublishRequest, request_id)
        if request is None:
            raise HTTPException(status_code=404, detail="publishing request not found")
        require_record_brand(session, actor, request)
        return complete_tiktok_draft(session, actor.id, request, outcome.upper(), post_url)

    @app.post(
        "/api/v1/publishing/clips/{clip_id}/review-gate", response_model=PublishReviewGateRead
    )
    def decide_publish_review_gate(
        clip_id: uuid.UUID,
        payload: PublishReviewGateRequest,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> PublishReviewGate:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.REVIEWER)
        clip = session.get(ProductionClip, clip_id)
        if clip is None:
            raise HTTPException(status_code=404, detail="production clip not found")
        require_record_brand(session, actor, clip)
        return set_review_gate(session, actor.id, clip, **payload.model_dump())

    @app.post(
        "/api/v1/publishing/requests",
        response_model=PublishRequestRead,
        status_code=status.HTTP_201_CREATED,
    )
    def create_publish_request(
        payload: PublishRequestCreate,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> PublishRequest:
        require_trusted_https_feature()
        require_role(actor, RoleName.OWNER, RoleName.ADMIN)
        clip = session.get(ProductionClip, payload.clip_id)
        package = session.get(ContentPackage, payload.content_package_id)
        destination = session.get(DestinationAccount, payload.destination_account_id)
        if clip is None or package is None or destination is None:
            raise HTTPException(
                status_code=404, detail="clip, content package, or destination account not found"
            )
        require_record_brand(session, actor, clip)
        require_record_brand(session, actor, package)
        require_record_brand(session, actor, destination)
        return request_publish(
            session,
            actor.id,
            clip,
            package,
            destination,
            payload.idempotency_key,
            payload.decision_type,
            payload.scheduled_for,
        )

    @app.post("/api/v1/publishing/requests/{request_id}/confirm", response_model=PublishRequestRead)
    def confirm_publish_request(
        request_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> PublishRequest:
        require_trusted_https_feature()
        require_role(actor, RoleName.OWNER, RoleName.ADMIN)
        request = session.get(PublishRequest, request_id)
        if request is None:
            raise HTTPException(status_code=404, detail="publishing request not found")
        require_record_brand(session, actor, request)
        confirmed = confirm_publish(session, actor.id, request)
        if confirmed.status == "QUEUED" and get_settings().publishing_enabled:
            from app.worker import celery_app

            celery_app.send_task("viralforge.execute_publish_request", args=[str(confirmed.id)])
        return confirmed

    @app.post("/api/v1/publishing/requests/{request_id}/cancel", response_model=PublishRequestRead)
    def cancel_publish_request(
        request_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> PublishRequest:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN)
        request = session.get(PublishRequest, request_id)
        if request is None:
            raise HTTPException(status_code=404, detail="publishing request not found")
        require_record_brand(session, actor, request)
        return cancel_publish(session, actor.id, request)

    @app.get("/api/v1/publishing/requests", response_model=list[PublishRequestRead])
    def publishing_history(
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
        brand_id: uuid.UUID | None = None,
    ) -> list[PublishRequest]:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.REVIEWER, RoleName.VIEWER)
        brand = brand_for_actor(session, actor.id, actor.roles, brand_id)
        return list(
            session.scalars(
                select(PublishRequest)
                .where(PublishRequest.brand_id == brand.id)
                .order_by(PublishRequest.created_at.desc())
                .limit(100)
            )
        )

    @app.get(
        "/api/v1/publishing/requests/{request_id}/attempts", response_model=list[PublishAttemptRead]
    )
    def publishing_attempt_history(
        request_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> list[PublishAttempt]:
        request = session.get(PublishRequest, request_id)
        if request is None:
            raise HTTPException(status_code=404, detail="publishing request not found")
        require_record_brand(session, actor, request)
        return list(
            session.scalars(
                select(PublishAttempt)
                .where(PublishAttempt.publish_request_id == request.id)
                .order_by(PublishAttempt.attempt_number)
            )
        )

    @app.post(
        "/api/v1/analytics/requests/{request_id}/snapshots",
        response_model=AnalyticsSnapshotRead,
        status_code=status.HTTP_201_CREATED,
    )
    def import_analytics_snapshot(
        request_id: uuid.UUID,
        payload: AnalyticsSnapshotImport,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> PostAnalyticsSnapshot:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.ANALYST)
        request = session.get(PublishRequest, request_id)
        if request is None:
            raise HTTPException(status_code=404, detail="publishing request not found")
        require_record_brand(session, actor, request)
        return persist_snapshot(
            session, request, NormalizedMetrics(**payload.model_dump()), "OPERATOR_IMPORT"
        )

    @app.post(
        "/api/v1/analytics/requests/{request_id}/feedback",
        response_model=FeedbackRead,
        status_code=status.HTTP_201_CREATED,
    )
    def add_analytics_feedback(
        request_id: uuid.UUID,
        payload: FeedbackCreate,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> OperatorFeedbackLabel:
        require_role(
            actor,
            RoleName.OWNER,
            RoleName.ADMIN,
            RoleName.EDITOR,
            RoleName.REVIEWER,
            RoleName.ANALYST,
        )
        request = session.get(PublishRequest, request_id)
        if request is None:
            raise HTTPException(status_code=404, detail="publishing request not found")
        require_record_brand(session, actor, request)
        return add_feedback(session, actor.id, request, **payload.model_dump())

    @app.post("/api/v1/analytics/refresh", response_model=dict[str, object])
    def refresh_analytics(
        brand_id: uuid.UUID | None,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> dict[str, object]:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.ANALYST)
        brand = brand_for_actor(session, actor.id, actor.roles, brand_id)
        run = refresh_brand(session, actor.id, brand.id)
        return {
            "id": str(run.id),
            "status": run.status,
            "processed_count": run.processed_count,
            "snapshot_count": run.snapshot_count,
            "error_summary": run.error_summary,
        }

    @app.get("/api/v1/analytics/dashboard", response_model=dict[str, object])
    def analytics_brand_dashboard(
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
        brand_id: uuid.UUID | None = None,
    ) -> dict[str, object]:
        require_role(
            actor,
            RoleName.OWNER,
            RoleName.ADMIN,
            RoleName.EDITOR,
            RoleName.REVIEWER,
            RoleName.ANALYST,
            RoleName.VIEWER,
        )
        brand = brand_for_actor(session, actor.id, actor.roles, brand_id)
        return dict(analytics_dashboard(session, brand.id))

    @app.get("/api/v1/production/queue", response_model=list[ProductionQueueRead])
    def production_queue(
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
        brand_id: uuid.UUID | None = None,
    ) -> list[PostingQueueItem]:
        require_role(
            actor,
            RoleName.OWNER,
            RoleName.ADMIN,
            RoleName.EDITOR,
            RoleName.REVIEWER,
            RoleName.ANALYST,
            RoleName.VIEWER,
        )
        brand = brand_for_actor(session, actor.id, actor.roles, brand_id)
        return list(
            session.scalars(
                select(PostingQueueItem)
                .where(PostingQueueItem.brand_id == brand.id)
                .order_by(PostingQueueItem.created_at.desc())
                .limit(100)
            )
        )

    @app.post(
        "/api/v1/discovery/sources",
        response_model=DiscoverySourceRead,
        status_code=status.HTTP_201_CREATED,
    )
    def create_discovery_source(
        payload: DiscoverySourceCreate,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> DiscoverySource:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN)
        brand = brand_for_actor(session, actor.id, actor.roles, payload.brand_id)
        values = payload.model_dump(exclude={"polling_interval_seconds", "brand_id"})
        source = DiscoverySource(
            **values,
            brand_id=brand.id,
            polling_interval_seconds=payload.polling_interval_seconds
            or get_settings().discovery_default_polling_interval_seconds,
        )
        session.add(source)
        session.flush()
        session.add(
            AuditEvent(
                actor_id=actor.id,
                entity_type="discovery_source",
                entity_id=source.id,
                event_name="discovery.source.created",
                brand_id=source.brand_id,
            )
        )
        session.commit()
        return source

    @app.get("/api/v1/discovery/sources", response_model=list[DiscoverySourceRead])
    def list_discovery_sources(
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
        brand_id: uuid.UUID | None = None,
    ) -> list[DiscoverySource]:
        require_role(
            actor,
            RoleName.OWNER,
            RoleName.ADMIN,
            RoleName.EDITOR,
            RoleName.REVIEWER,
            RoleName.ANALYST,
            RoleName.VIEWER,
        )
        brand = brand_for_actor(session, actor.id, actor.roles, brand_id)
        return list(
            session.scalars(
                select(DiscoverySource)
                .where(DiscoverySource.brand_id == brand.id)
                .order_by(DiscoverySource.created_at.desc())
                .limit(100)
            )
        )

    @app.post("/api/v1/discovery/sources/{source_id}/run", response_model=DiscoveryRunRead)
    def run_discovery_source(
        source_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> DiscoveryRun:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR)
        source = session.get(DiscoverySource, source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="discovery source not found")
        require_record_brand(session, actor, source)
        return run_source(session, actor.id, source)

    @app.get("/api/v1/discovery/runs", response_model=list[DiscoveryRunRead])
    def list_discovery_runs(
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
        source_id: uuid.UUID | None = None,
        brand_id: uuid.UUID | None = None,
    ) -> list[DiscoveryRun]:
        require_role(
            actor,
            RoleName.OWNER,
            RoleName.ADMIN,
            RoleName.EDITOR,
            RoleName.REVIEWER,
            RoleName.ANALYST,
            RoleName.VIEWER,
        )
        brand = brand_for_actor(session, actor.id, actor.roles, brand_id)
        statement = select(DiscoveryRun).where(DiscoveryRun.brand_id == brand.id)
        if source_id:
            statement = statement.where(DiscoveryRun.discovery_source_id == source_id)
        return list(session.scalars(statement.order_by(DiscoveryRun.started_at.desc()).limit(100)))

    @app.get("/api/v1/discovery/media", response_model=list[DiscoveredMediaRead])
    def list_discovered_media(
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
        lifecycle_status: str | None = None,
        brand_id: uuid.UUID | None = None,
    ) -> list[DiscoveredMedia]:
        require_role(
            actor,
            RoleName.OWNER,
            RoleName.ADMIN,
            RoleName.EDITOR,
            RoleName.REVIEWER,
            RoleName.ANALYST,
            RoleName.VIEWER,
        )
        brand = brand_for_actor(session, actor.id, actor.roles, brand_id)
        statement = select(DiscoveredMedia).where(DiscoveredMedia.brand_id == brand.id)
        if lifecycle_status:
            statement = statement.where(DiscoveredMedia.lifecycle_status == lifecycle_status)
        return list(
            session.scalars(statement.order_by(DiscoveredMedia.discovered_at.desc()).limit(100))
        )

    @app.get("/api/v1/discovery/media/{media_id}", response_model=DiscoveredMediaRead)
    def get_discovered_media(
        media_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> DiscoveredMedia:
        require_role(
            actor,
            RoleName.OWNER,
            RoleName.ADMIN,
            RoleName.EDITOR,
            RoleName.REVIEWER,
            RoleName.ANALYST,
            RoleName.VIEWER,
        )
        media = session.get(DiscoveredMedia, media_id)
        if media is None:
            raise HTTPException(status_code=404, detail="discovered media not found")
        return media

    @app.post("/api/v1/discovery/media/{media_id}/approve", response_model=DiscoveredMediaRead)
    def approve_discovered_media(
        media_id: uuid.UUID,
        payload: DiscoveryDecisionRequest,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> DiscoveredMedia:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR, RoleName.REVIEWER)
        media = session.get(DiscoveredMedia, media_id)
        if media is None:
            raise HTTPException(status_code=404, detail="discovered media not found")
        return approve_media(session, actor.id, media, payload.expected_version)

    @app.post("/api/v1/discovery/media/{media_id}/reject", response_model=DiscoveredMediaRead)
    def reject_discovered_media(
        media_id: uuid.UUID,
        payload: DiscoveryDecisionRequest,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> DiscoveredMedia:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR, RoleName.REVIEWER)
        media = session.get(DiscoveredMedia, media_id)
        if media is None:
            raise HTTPException(status_code=404, detail="discovered media not found")
        return reject_media(session, actor.id, media, payload.expected_version, payload.reason)

    @app.get("/api/v1/discovery/providers/health")
    def discovery_provider_health(
        actor: Annotated[Actor, Depends(development_actor)],
    ) -> dict[str, object]:
        require_role(
            actor,
            RoleName.OWNER,
            RoleName.ADMIN,
            RoleName.EDITOR,
            RoleName.REVIEWER,
            RoleName.ANALYST,
            RoleName.VIEWER,
        )
        return {
            name: provider.capabilities().__dict__
            for name, provider in __import__(
                "app.discovery.providers", fromlist=["default_providers"]
            )
            .default_providers()
            .items()
        }

    def feed_read(feed: FeedSubscription, session: Session) -> FeedRead:
        policy = session.scalar(
            select(SourcePolicy)
            .where(SourcePolicy.source_id == feed.source_id)
            .order_by(SourcePolicy.created_at.desc())
        )
        lease_active = bool(
            feed.active_lease_until
            and ensure_utc(feed.active_lease_until)
            > datetime.now(ensure_utc(feed.active_lease_until).tzinfo)
        )
        return FeedRead.model_validate(feed, from_attributes=True).model_copy(
            update={
                "etag_present": bool(feed.etag),
                "last_modified_present": bool(feed.last_modified),
                "lease_active": lease_active,
                "next_eligible_run": next_eligible_run(feed, policy),
            }
        )

    @app.post("/api/v1/feeds", response_model=FeedRead, status_code=status.HTTP_201_CREATED)
    async def create_feed(
        payload: FeedCreate,
        request: Request,
        response: Response,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
        client: Annotated[SafeOutboundHttpClient, Depends(get_feed_client)],
    ) -> FeedRead:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN)
        if payload.idempotency_key:
            existing = session.scalar(
                select(FeedSubscription).where(
                    FeedSubscription.idempotency_key == payload.idempotency_key
                )
            )
            if existing is not None:
                if (
                    existing.source_id != payload.source_id
                    or existing.feed_url != payload.feed_url.strip().lower()
                ):
                    raise FeedError(
                        "IDEMPOTENCY_CONFLICT", "idempotency key was used with different feed data"
                    )
                response.status_code = status.HTTP_200_OK
                return feed_read(existing, session)
        feed = await register_feed(
            session,
            actor.id,
            payload.source_id,
            payload.feed_url,
            client,
            request.headers.get("X-Correlation-ID"),
            payload.polling_interval_seconds,
            payload.recent_item_window_days,
            payload.max_items_per_run,
            payload.notes,
            payload.idempotency_key,
        )
        if feed.status != "ACTIVE":
            response.status_code = status.HTTP_400_BAD_REQUEST
        return feed_read(feed, session)

    @app.get("/api/v1/feeds", response_model=FeedPage)
    def list_feeds(
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
        page: int = 1,
        page_size: int = 25,
        source_id: uuid.UUID | None = None,
        feed_status: str | None = None,
        feed_type: str | None = None,
        active: bool | None = None,
        failing: bool | None = None,
    ) -> FeedPage:
        require_role(
            actor,
            RoleName.OWNER,
            RoleName.ADMIN,
            RoleName.EDITOR,
            RoleName.REVIEWER,
            RoleName.ANALYST,
            RoleName.VIEWER,
        )
        if page < 1 or not 1 <= page_size <= 100:
            raise HTTPException(
                status_code=422, detail="page must be positive and page_size must be 1..100"
            )
        statement = select(FeedSubscription)
        if source_id is not None:
            statement = statement.where(FeedSubscription.source_id == source_id)
        if feed_status is not None:
            statement = statement.where(FeedSubscription.status == feed_status)
        if feed_type is not None:
            statement = statement.where(FeedSubscription.feed_type == feed_type)
        if active is True:
            statement = statement.where(FeedSubscription.status == "ACTIVE")
        if failing is True:
            statement = statement.where(FeedSubscription.status == "FAILING")
        total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        items = list(
            session.scalars(
                statement.order_by(FeedSubscription.created_at.desc(), FeedSubscription.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return FeedPage(
            items=[feed_read(item, session) for item in items],
            total=total,
            page=page,
            page_size=page_size,
        )

    @app.get("/api/v1/feeds/{feed_id}", response_model=FeedRead)
    def get_feed(
        feed_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> FeedRead:
        require_role(
            actor,
            RoleName.OWNER,
            RoleName.ADMIN,
            RoleName.EDITOR,
            RoleName.REVIEWER,
            RoleName.ANALYST,
            RoleName.VIEWER,
        )
        feed = session.get(FeedSubscription, feed_id)
        if feed is None:
            raise HTTPException(status_code=404, detail="feed not found")
        return feed_read(feed, session)

    @app.patch("/api/v1/feeds/{feed_id}", response_model=FeedRead)
    def update_feed(
        feed_id: uuid.UUID,
        payload: FeedPatch,
        request: Request,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> FeedRead:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN)
        feed = session.get(FeedSubscription, feed_id)
        if feed is None:
            raise HTTPException(status_code=404, detail="feed not found")
        if payload.version_id != feed.version_id:
            raise HTTPException(
                status_code=409, detail="feed was changed concurrently; retry with current state"
            )
        updates = payload.model_dump(exclude={"version_id"}, exclude_unset=True)
        for field, value in updates.items():
            setattr(feed, field, value)
        feed.version_id += 1
        session.add(
            AuditEvent(
                actor_id=actor.id,
                entity_type="feed",
                entity_id=feed.id,
                event_name="feed.updated",
                correlation_id=request.headers.get("X-Correlation-ID"),
                payload={"fields": sorted(updates)},
            )
        )
        session.commit()
        return feed_read(feed, session)

    @app.post("/api/v1/feeds/{feed_id}/validate", response_model=FeedRead)
    async def revalidate_feed(
        feed_id: uuid.UUID,
        request: Request,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
        client: Annotated[SafeOutboundHttpClient, Depends(get_feed_client)],
    ) -> FeedRead:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN)
        feed = session.get(FeedSubscription, feed_id)
        if feed is None:
            raise HTTPException(status_code=404, detail="feed not found")
        feed = await validate_feed(
            session, actor.id, feed, client, request.headers.get("X-Correlation-ID")
        )
        return feed_read(feed, session)

    @app.post("/api/v1/feeds/{feed_id}/run", response_model=IngestionJobRead)
    async def execute_feed(
        feed_id: uuid.UUID,
        payload: FeedRunRequest,
        request: Request,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
        client: Annotated[SafeOutboundHttpClient, Depends(get_feed_client)],
    ) -> IngestionJob:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR)
        if (
            payload.recent_item_window_days is not None or payload.max_items_per_run is not None
        ) and not actor.roles.intersection({RoleName.OWNER, RoleName.ADMIN}):
            raise HTTPException(
                status_code=403, detail="administrative run overrides require owner or admin"
            )
        if payload.idempotency_key:
            existing = session.scalar(
                select(IngestionJob).where(IngestionJob.idempotency_key == payload.idempotency_key)
            )
            if existing is not None:
                return existing
        job = await run_feed(
            session,
            actor.id,
            feed_id,
            client,
            request.headers.get("X-Correlation-ID"),
            payload.recent_item_window_days,
            payload.max_items_per_run,
        )
        if payload.idempotency_key:
            job.idempotency_key = payload.idempotency_key
            session.commit()
        return job

    def set_feed_status(
        feed_id: uuid.UUID,
        target: str,
        request: Request,
        actor: Actor,
        session: Session,
        reason: str | None = None,
    ) -> FeedRead:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN)
        feed = session.get(FeedSubscription, feed_id)
        if feed is None:
            raise HTTPException(status_code=404, detail="feed not found")
        if target == "block":
            require_role(actor, RoleName.ADMIN)
        return feed_read(
            change_feed_status(
                session, actor.id, feed, target, request.headers.get("X-Correlation-ID"), reason
            ),
            session,
        )

    @app.post("/api/v1/feeds/{feed_id}/pause", response_model=FeedRead)
    def pause_feed(
        feed_id: uuid.UUID,
        request: Request,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> FeedRead:
        return set_feed_status(feed_id, "pause", request, actor, session)

    @app.post("/api/v1/feeds/{feed_id}/activate", response_model=FeedRead)
    def activate_feed_status(
        feed_id: uuid.UUID,
        request: Request,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> FeedRead:
        return set_feed_status(feed_id, "activate", request, actor, session)

    @app.post("/api/v1/feeds/{feed_id}/block", response_model=FeedRead)
    def block_feed(
        feed_id: uuid.UUID,
        payload: FeedBlockRequest,
        request: Request,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> FeedRead:
        return set_feed_status(feed_id, "block", request, actor, session, payload.reason)

    @app.get("/api/v1/feeds/{feed_id}/entries", response_model=FeedEntryPage)
    def list_feed_entries(
        feed_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
        page: int = 1,
        page_size: int = 25,
        imported: bool | None = None,
    ) -> FeedEntryPage:
        require_role(
            actor,
            RoleName.OWNER,
            RoleName.ADMIN,
            RoleName.EDITOR,
            RoleName.REVIEWER,
            RoleName.ANALYST,
            RoleName.VIEWER,
        )
        if page < 1 or not 1 <= page_size <= 100:
            raise HTTPException(
                status_code=422, detail="page must be positive and page_size must be 1..100"
            )
        if session.get(FeedSubscription, feed_id) is None:
            raise HTTPException(status_code=404, detail="feed not found")
        statement = select(FeedEntry).where(FeedEntry.subscription_id == feed_id)
        if imported is True:
            statement = statement.where(FeedEntry.import_outcome == "IMPORTED")
        if imported is False:
            statement = statement.where(FeedEntry.import_outcome != "IMPORTED")
        total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        items = list(
            session.scalars(
                statement.order_by(
                    FeedEntry.published_at.desc(), FeedEntry.created_at.desc(), FeedEntry.id.desc()
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return FeedEntryPage(
            items=[FeedEntryRead.model_validate(item) for item in items],
            total=total,
            page=page,
            page_size=page_size,
        )

    @app.get("/api/v1/feeds/{feed_id}/runs", response_model=FeedRunPage)
    def list_feed_runs(
        feed_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
        page: int = 1,
        page_size: int = 25,
    ) -> FeedRunPage:
        require_role(
            actor,
            RoleName.OWNER,
            RoleName.ADMIN,
            RoleName.EDITOR,
            RoleName.REVIEWER,
            RoleName.ANALYST,
            RoleName.VIEWER,
        )
        feed = session.get(FeedSubscription, feed_id)
        if feed is None:
            raise HTTPException(status_code=404, detail="feed not found")
        statement = select(IngestionJob).where(
            IngestionJob.source_id == feed.source_id,
            IngestionJob.method.in_([IngestionMethod.RSS_FEED, IngestionMethod.ATOM_FEED]),
        )
        total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        items = list(
            session.scalars(
                statement.order_by(IngestionJob.created_at.desc(), IngestionJob.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return FeedRunPage(
            items=[FeedRunRead.model_validate(item) for item in items],
            total=total,
            page=page,
            page_size=page_size,
        )

    @app.get("/api/v1/ingestion/jobs", response_model=list[IngestionJobRead])
    def list_ingestion_jobs(
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
        page: int = 1,
        page_size: int = 25,
    ) -> list[IngestionJob]:
        require_role(
            actor,
            RoleName.OWNER,
            RoleName.ADMIN,
            RoleName.EDITOR,
            RoleName.REVIEWER,
            RoleName.ANALYST,
            RoleName.VIEWER,
        )
        return list(
            session.scalars(
                select(IngestionJob)
                .order_by(IngestionJob.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )

    @app.get("/api/v1/ingestion/jobs/{job_id}", response_model=IngestionJobRead)
    def get_ingestion_job(
        job_id: uuid.UUID,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> IngestionJob:
        require_role(
            actor,
            RoleName.OWNER,
            RoleName.ADMIN,
            RoleName.EDITOR,
            RoleName.REVIEWER,
            RoleName.ANALYST,
            RoleName.VIEWER,
        )
        job = session.get(IngestionJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="ingestion job not found")
        return job

    @app.post("/api/v1/sources", response_model=SourceRead, status_code=status.HTTP_201_CREATED)
    def create_source(
        payload: SourceCreate,
        request: Request,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> Source:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN)
        source = Source(
            platform=Platform.MANUAL,
            normalized_url=payload.normalized_url,
            source_type=payload.source_type,
            status=SourceStatus.PENDING_REVIEW,
        )
        session.add(source)
        session.add(
            AuditEvent(
                actor_id=actor.id,
                entity_type="source",
                entity_id=source.id,
                event_name="source.created",
                correlation_id=request.headers.get("X-Correlation-ID"),
            )
        )
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            raise HTTPException(status_code=409, detail="source already exists") from error
        session.refresh(source)
        return source

    @app.post("/api/v1/sources/{source_id}/activate", response_model=SourceRead)
    def activate_source(
        source_id: uuid.UUID,
        request: Request,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> Source:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN)
        source = session.get(Source, source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="source not found")
        change_source_status(
            session, source, SourceStatus.ACTIVE, actor.id, request.headers.get("X-Correlation-ID")
        )
        session.commit()
        session.refresh(source)
        return source

    @app.get("/api/v1/content", response_model=ContentPage)
    def list_content(
        session: Annotated[Session, Depends(get_session)], page: int = 1, page_size: int = 25
    ) -> ContentPage:
        if page < 1 or not 1 <= page_size <= 100:
            raise HTTPException(
                status_code=422, detail="page must be positive and page_size must be 1..100"
            )
        total = session.scalar(select(func.count()).select_from(ContentItem)) or 0
        items = list(
            session.scalars(
                select(ContentItem)
                .order_by(ContentItem.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return ContentPage(
            items=[ContentRead.model_validate(item) for item in items],
            total=total,
            page=page,
            page_size=page_size,
        )

    @app.get("/api/v1/content/{content_id}", response_model=ContentRead)
    def get_content(
        content_id: uuid.UUID, session: Annotated[Session, Depends(get_session)]
    ) -> ContentItem:
        item = session.get(ContentItem, content_id)
        if item is None:
            raise HTTPException(status_code=404, detail="content item not found")
        return item

    @app.get("/api/v1/content/{content_id}/audit", response_model=list[AuditRead])
    def get_audit(
        content_id: uuid.UUID, session: Annotated[Session, Depends(get_session)]
    ) -> list[AuditEvent]:
        if session.get(ContentItem, content_id) is None:
            raise HTTPException(status_code=404, detail="content item not found")
        return list(
            session.scalars(
                select(AuditEvent)
                .where(AuditEvent.entity_type == "content_item", AuditEvent.entity_id == content_id)
                .order_by(AuditEvent.created_at)
            )
        )

    @app.post("/api/v1/content/{content_id}/transition", response_model=ContentRead)
    def transition_content(
        content_id: uuid.UUID,
        payload: TransitionRequest,
        request: Request,
        actor: Annotated[Actor, Depends(development_actor)],
        session: Annotated[Session, Depends(get_session)],
    ) -> ContentItem:
        require_role(actor, RoleName.OWNER, RoleName.ADMIN, RoleName.EDITOR, RoleName.REVIEWER)
        item = session.get(ContentItem, content_id)
        if item is None:
            raise HTTPException(status_code=404, detail="content item not found")
        try:
            result = transition(
                session,
                item,
                payload.target_status,
                actor.id,
                payload.reason,
                request.headers.get("X-Correlation-ID"),
            )
            session.commit()
        except StaleDataError as error:
            session.rollback()
            raise HTTPException(
                status_code=409,
                detail="content item was changed concurrently; retry with current state",
            ) from error
        session.refresh(result)
        return result

    return app


app = create_app()
