from enum import StrEnum
from functools import lru_cache
from ipaddress import ip_address
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class DeploymentMode(StrEnum):
    DEVELOPMENT = "development"
    IP_BOOTSTRAP = "ip_bootstrap"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="VIRALFORGE_", extra="ignore")

    environment: Environment = Environment.DEVELOPMENT
    deployment_mode: DeploymentMode = DeploymentMode.DEVELOPMENT
    database_url: str = "sqlite+pysqlite:///./viralforge.db"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    api_secret: str = "change-me-development-only"
    enable_development_actor: bool = True
    service_name: str = "viralforge-api"
    object_storage_endpoint: str | None = None
    object_storage_bucket: str | None = None
    object_storage_access_key: str | None = None
    object_storage_secret_key: str | None = None
    discord_bot_token: str | None = None
    discord_guild_id: str | None = None
    discord_review_channel_id: str | None = None
    discord_allowed_role_ids: str | None = None
    discord_max_preview_upload_bytes: int = Field(default=25_000_000, ge=1_024)
    api_base_url: str = "http://localhost:8000"
    public_base_url: str = "http://localhost:8000"
    public_host: str = ""
    ip_bootstrap_port: int = Field(default=8081, ge=1, le=65535)
    oauth_callback_base_url: str = "http://localhost:8000"
    trusted_hosts: str = ""
    cors_allowed_origins: str = ""
    discord_enabled: bool = False
    scheduler_enabled: bool = True
    scheduler_heartbeat_interval_seconds: int = Field(default=60, ge=10, le=3600)
    preview_enabled: bool = True
    preview_public_base_url: str = "http://localhost:8000"
    preview_token_ttl_seconds: int = Field(default=86_400, ge=60, le=2_592_000)
    preview_maximum_access_count: int = Field(default=100, ge=1, le=10_000)
    preview_hashing_secret: str = "change-me-development-preview-secret"
    preview_page_title: str = "ViralForge Private Preview"
    preview_stream_chunk_bytes: int = Field(default=262_144, ge=16_384, le=4_194_304)
    preview_proxy_enabled: bool = False
    preview_proxy_width: int = Field(default=540, ge=64, le=1920)
    preview_proxy_height: int = Field(default=960, ge=64, le=1920)
    preview_proxy_video_bitrate: str = "900k"
    preview_proxy_audio_bitrate: str = "96k"
    preview_proxy_fps: int = Field(default=30, ge=1, le=60)
    preview_proxy_max_bytes: int = Field(default=100_000_000, ge=1_024)
    preview_proxy_timeout_seconds: int = Field(default=600, ge=10, le=3600)
    preview_retention_temporary_seconds: int = Field(default=21_600, ge=60)
    preview_retention_proxy_seconds: int = Field(default=86_400, ge=60)
    preview_retention_rejected_seconds: int = Field(default=86_400, ge=60)
    preview_retention_unreviewed_seconds: int = Field(default=259_200, ge=60)
    preview_retention_approved_seconds: int = Field(default=259_200, ge=60)
    preview_retention_published_seconds: int = Field(default=86_400, ge=60)
    preview_retention_failed_seconds: int = Field(default=604_800, ge=60)
    preview_retention_source_seconds: int = Field(default=172_800, ge=60)
    cleanup_enabled: bool = True
    cleanup_dry_run: bool = False
    cleanup_batch_size: int = Field(default=100, ge=1, le=1000)
    cleanup_interval_seconds: int = Field(default=3600, ge=60, le=86_400)
    storage_warning_threshold_percent: int = Field(default=75, ge=1, le=99)
    storage_critical_threshold_percent: int = Field(default=90, ge=1, le=99)
    storage_emergency_threshold_percent: int = Field(default=97, ge=1, le=100)
    storage_emergency_cleanup_enabled: bool = False
    # TikTok credentials are always referenced externally.  The legacy raw
    # client-secret setting remains ignored for compatibility and must not be
    # used by the publishing provider.
    tiktok_client_id: str | None = None
    tiktok_client_secret: str | None = None
    tiktok_client_secret_credential_reference: str | None = None
    credential_store_backend: str = "env"
    credential_store_file_path: str = "/data/credentials/viralforge-credentials.json"
    credential_store_master_key_reference: str | None = None
    tiktok_enabled: bool = False
    tiktok_draft_upload_enabled: bool = False
    tiktok_direct_post_enabled: bool = False
    tiktok_public_direct_post_enabled: bool = False
    download_token_ttl_seconds: int = 900
    download_maximum_access_count: int = 2
    tiktok_default_mode: str = "DRAFT_UPLOAD"
    tiktok_application_review_state: str = "DEVELOPMENT"
    tiktok_oauth_state_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    tiktok_oauth_state_secret: str | None = None
    tiktok_request_timeout_seconds: int = Field(default=30, ge=1, le=300)
    tiktok_upload_timeout_seconds: int = Field(default=1800, ge=1, le=14400)
    tiktok_status_poll_interval_seconds: int = Field(default=60, ge=10, le=3600)
    tiktok_max_status_poll_attempts: int = Field(default=30, ge=1, le=720)
    tiktok_retry_count: int = Field(default=3, ge=0, le=10)
    tiktok_retry_backoff_seconds: int = Field(default=60, ge=1, le=3600)
    tiktok_transfer_chunk_size_bytes: int = Field(default=10_000_000, ge=5_000_000, le=64_000_000)
    tiktok_max_media_bytes: int = Field(default=4_000_000_000, ge=5_000_000, le=4_000_000_000)
    tiktok_max_transfers_per_day: int = Field(default=3, ge=1, le=100)
    tiktok_minimum_transfer_interval_seconds: int = Field(default=300, ge=0, le=86400)
    tiktok_max_pending_drafts: int = Field(default=5, ge=1, le=100)
    tiktok_credential_refresh_window_seconds: int = Field(default=86400, ge=60, le=604800)
    tiktok_emergency_pause: bool = True
    tiktok_pilot_brand_slug: str | None = None
    youtube_api_key: str | None = None
    youtube_search_max_results: int = Field(default=5, ge=1, le=25)
    youtube_search_default_order: str = "relevance"
    youtube_client_id: str | None = None
    youtube_client_secret: str | None = None
    youtube_oauth_enabled: bool = False
    meta_app_id: str | None = None
    meta_app_secret: str | None = None
    transcription_provider_api_key: str | None = None
    analysis_provider_api_key: str | None = None
    ingestion_http_user_agent: str = "ViralForgeMetadataBot/0.1 (+https://viralforge.invalid)"
    ingestion_http_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    ingestion_http_read_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    ingestion_http_write_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    ingestion_http_pool_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    ingestion_http_total_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    ingestion_http_max_redirects: int = Field(default=3, ge=0, le=10)
    ingestion_http_max_response_bytes: int = Field(default=1_000_000, ge=1024, le=10_000_000)
    storage_provider: str = "local"
    local_storage_root: str = "../viralforge-data/uploads"
    upload_max_bytes: int = Field(default=104_857_600, ge=1_024, le=2_147_483_647)
    upload_chunk_bytes: int = Field(default=262_144, ge=1_024, le=4_194_304)
    upload_filename_max_length: int = Field(default=255, ge=1, le=500)
    upload_notes_max_length: int = Field(default=2_000, ge=1, le=10_000)
    upload_temp_max_age_seconds: int = Field(default=86_400, ge=60, le=2_592_000)
    feed_default_recent_window_days: int = Field(default=30, ge=1, le=365)
    feed_min_recent_window_days: int = Field(default=1, ge=1, le=365)
    feed_max_recent_window_days: int = Field(default=90, ge=1, le=365)
    feed_absolute_max_historical_age_days: int = Field(default=365, ge=1, le=3650)
    feed_default_max_items_per_run: int = Field(default=20, ge=1, le=1000)
    feed_absolute_max_items_per_run: int = Field(default=100, ge=1, le=1000)
    feed_min_polling_interval_seconds: int = Field(default=60, ge=1, le=86_400)
    feed_max_parsed_entries: int = Field(default=200, ge=1, le=2000)
    feed_future_date_skew_seconds: int = Field(default=300, ge=0, le=86_400)
    video_download_provider: str = "yt_dlp"
    video_download_executable: str = "yt-dlp"
    ytdlp_path: str | None = None
    video_download_timeout_seconds: int = Field(default=1_800, ge=1, le=14_400)
    video_download_max_bytes: int = Field(default=2_147_483_648, ge=1_024)
    video_work_root: str = "../viralforge-data/production"
    video_output_format: str = "mp4"
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    # Rendered-media inspection is deliberately local and advisory by default.
    # Automatic inspection remains opt-in per brand profile.
    rendered_media_inspection_enabled: bool = True
    rendered_media_inspection_auto_run: bool = False
    rendered_media_inspection_provider: str = "local_ffmpeg"
    rendered_media_inspection_safe_area_profile: str = "generic_9_16"
    rendered_media_inspection_sampling_interval_seconds: float = Field(default=3.0, gt=0, le=60)
    rendered_media_inspection_max_samples: int = Field(default=30, ge=4, le=120)
    rendered_media_inspection_timeout_seconds: int = Field(default=300, ge=10, le=3600)
    rendered_media_inspection_ocr_enabled: bool = False
    rendered_media_inspection_cv_enabled: bool = False
    rendered_media_inspection_temp_max_age_seconds: int = Field(default=86400, ge=60, le=604800)
    max_source_duration_seconds: int = Field(default=14_400, ge=1)
    default_clip_duration_seconds: int = Field(default=45, ge=1, le=600)
    min_clip_duration_seconds: int = Field(default=15, ge=1, le=600)
    max_clip_duration_seconds: int = Field(default=60, ge=1, le=600)
    clip_overlap_seconds: int = Field(default=0, ge=0, le=300)
    output_width: int = Field(default=1080, ge=2, le=4096)
    output_height: int = Field(default=1920, ge=2, le=4096)
    output_fps: int = Field(default=30, ge=1, le=120)
    output_video_codec: str = "libx264"
    output_audio_codec: str = "aac"
    source_resolution_enabled: bool = True
    source_max_candidate_count: int = Field(default=5, ge=1, le=25)
    source_search_timeout_seconds: int = Field(default=15, ge=1, le=120)
    source_min_original_confidence: float = Field(default=0.70, ge=0, le=1)
    source_min_accepted_quality_score: float = Field(default=65.0, ge=0, le=100)
    source_watermark_review_threshold: float = Field(default=0.55, ge=0, le=1)
    source_duplicate_similarity_threshold: float = Field(default=0.82, ge=0, le=1)
    source_preferred_min_width: int = Field(default=1280, ge=1, le=7680)
    source_preferred_platforms: str = "YOUTUBE"
    source_official_registry_path: str = "config/official_sources.yml"
    source_quality_weights_path: str = "config/source_quality_weights.yml"
    source_candidate_cache_seconds: int = Field(default=3_600, ge=0, le=86_400)
    source_sampled_watermark_frame_count: int = Field(default=5, ge=1, le=60)
    discovery_enabled: bool = False
    discovery_scheduler_enabled: bool = False
    discovery_default_polling_interval_seconds: int = Field(default=3600, ge=60, le=86_400)
    discovery_provider_concurrency: int = Field(default=2, ge=1, le=20)
    discovery_per_source_concurrency: int = Field(default=1, ge=1, le=10)
    discovery_request_timeout_seconds: int = Field(default=20, ge=1, le=120)
    discovery_retry_count: int = Field(default=3, ge=0, le=10)
    discovery_retry_backoff_seconds: int = Field(default=60, ge=1, le=3600)
    discovery_result_limit: int = Field(default=20, ge=1, le=100)
    discovery_min_relevance_score: float = Field(default=45, ge=0, le=100)
    discovery_manual_review_threshold: float = Field(default=75, ge=0, le=100)
    discovery_trusted_source_auto_process: bool = False
    discovery_max_item_age_days: int = Field(default=30, ge=1, le=365)
    discovery_min_video_duration_seconds: int = Field(default=10, ge=0, le=3600)
    discovery_max_video_duration_seconds: int = Field(default=14_400, ge=1, le=86_400)
    discovery_duplicate_similarity_threshold: float = Field(default=0.82, ge=0, le=1)
    discovery_review_channel_id: str | None = None
    discovery_relevance_weights_path: str = "config/discovery_relevance_weights.yml"
    analysis_enabled: bool = True
    analysis_max_concurrency: int = Field(default=1, ge=1, le=10)
    analysis_video_provider: str = "ffprobe"
    analysis_transcript_provider: str = "mock"
    analysis_ocr_provider: str = "mock"
    analysis_version: str = "real-media-v1"
    analysis_transcript_model: str = "tiny"
    analysis_transcript_device: str = "cpu"
    analysis_transcript_compute_type: str = "int8"
    analysis_transcript_language: str | None = None
    analysis_transcript_word_timestamps: bool = True
    analysis_transcript_beam_size: int = Field(default=3, ge=1, le=10)
    analysis_transcript_vad_enabled: bool = True
    analysis_transcript_timeout_seconds: int = Field(default=900, ge=1, le=14_400)
    analysis_model_cache_root: str = "../viralforge-data/models"
    analysis_silence_noise_threshold: str = "-35dB"
    analysis_min_silence_duration_seconds: float = Field(default=0.5, gt=0, le=60)
    analysis_min_speech_duration_seconds: float = Field(default=0.4, gt=0, le=60)
    analysis_merge_gap_seconds: float = Field(default=0.25, ge=0, le=10)
    analysis_long_silence_seconds: float = Field(default=2.0, gt=0, le=600)
    analysis_scene_min_duration_seconds: float = Field(default=0.5, gt=0, le=60)
    analysis_scene_max_sampled_frames: int = Field(default=2_000, ge=1, le=100_000)
    analysis_audio_sample_interval_seconds: float = Field(default=1.0, gt=0, le=30)
    analysis_audio_loudness_threshold_db: float = Field(default=-20.0, ge=-100, le=0)
    analysis_audio_peak_threshold_db: float = Field(default=-6.0, ge=-100, le=0)
    analysis_audio_min_event_duration_seconds: float = Field(default=0.5, gt=0, le=60)
    analysis_motion_provider: str = "ffmpeg"
    analysis_motion_sample_interval_seconds: float = Field(default=1.0, gt=0, le=30)
    analysis_motion_sample_width: int = Field(default=320, ge=32, le=1920)
    analysis_motion_max_samples: int = Field(default=2_000, ge=1, le=100_000)
    analysis_motion_high_threshold: float = Field(default=0.12, ge=0, le=1)
    analysis_motion_low_threshold: float = Field(default=0.02, ge=0, le=1)
    analysis_ocr_enabled: bool = False
    analysis_ocr_sample_count: int = Field(default=12, ge=1, le=100)
    analysis_timeline_max_events: int = Field(default=5_000, ge=10, le=100_000)
    analysis_frame_sampling_interval_seconds: float = Field(default=5.0, gt=0, le=60)
    analysis_scene_detection_threshold: float = Field(default=0.35, ge=0, le=1)
    analysis_timeout_seconds: int = Field(default=900, ge=1, le=14_400)
    analysis_max_video_duration_seconds: int = Field(default=14_400, ge=1)
    opportunity_enabled: bool = True
    opportunity_provider: str = "rule"
    opportunity_min_score: float = Field(default=35.0, ge=0, le=100)
    opportunity_max_count: int = Field(default=10, ge=1, le=50)
    opportunity_min_duration_seconds: int = Field(default=15, ge=1, le=600)
    opportunity_max_duration_seconds: int = Field(default=90, ge=1, le=600)
    opportunity_padding_before_seconds: float = Field(default=5.0, ge=0, le=120)
    opportunity_padding_after_seconds: float = Field(default=8.0, ge=0, le=120)
    opportunity_merge_overlap: float = Field(default=0.60, ge=0, le=1)
    opportunity_fallback_behavior: str = "timeline"
    opportunity_speech_weight: float = Field(default=0.15, ge=0, le=1)
    opportunity_motion_weight: float = Field(default=0.12, ge=0, le=1)
    opportunity_scene_weight: float = Field(default=0.12, ge=0, le=1)
    opportunity_transcript_weight: float = Field(default=0.15, ge=0, le=1)
    opportunity_ocr_weight: float = Field(default=0.08, ge=0, le=1)
    opportunity_audio_weight: float = Field(default=0.12, ge=0, le=1)
    opportunity_silence_weight: float = Field(default=0.06, ge=0, le=1)
    opportunity_event_weight: float = Field(default=0.14, ge=0, le=1)
    opportunity_visual_weight: float = Field(default=0.12, ge=0, le=1)
    content_package_enabled: bool = True
    content_package_provider: str = "mock"
    content_package_external_endpoint: str | None = None
    content_package_external_api_key: str | None = None
    content_package_timeout_seconds: int = Field(default=60, ge=1, le=600)
    content_package_max_transcript_chars: int = Field(default=600, ge=80, le=10_000)
    # Publishing is opt-in.  Credentials are only resolved at runtime from an opaque
    # external ``env://NAME`` reference held by a destination account.
    publishing_enabled: bool = False
    publishing_youtube_enabled: bool = False
    publishing_http_timeout_seconds: int = Field(default=30, ge=1, le=300)
    publishing_upload_timeout_seconds: int = Field(default=1_800, ge=1, le=14_400)
    publishing_media_probe_timeout_seconds: int = Field(default=30, ge=1, le=300)
    publishing_max_attempts: int = Field(default=3, ge=1, le=10)
    publishing_retry_backoff_seconds: int = Field(default=60, ge=1, le=3_600)
    analytics_enabled: bool = False
    analytics_youtube_enabled: bool = False
    analytics_http_timeout_seconds: int = Field(default=30, ge=1, le=300)
    analytics_refresh_batch_size: int = Field(default=25, ge=1, le=100)
    page_size_default: int = Field(default=25, ge=1, le=100)

    @model_validator(mode="after")
    def validate_production(self) -> "Settings":
        if (
            not self.feed_min_recent_window_days
            <= self.feed_default_recent_window_days
            <= self.feed_max_recent_window_days
            <= self.feed_absolute_max_historical_age_days
        ):
            raise ValueError("feed recent-item window settings must be ordered and bounded")
        if self.feed_default_max_items_per_run > self.feed_absolute_max_items_per_run:
            raise ValueError("feed default maximum items must not exceed its absolute maximum")
        if (
            not self.min_clip_duration_seconds
            <= self.default_clip_duration_seconds
            <= self.max_clip_duration_seconds
        ):
            raise ValueError("default clip duration must be within the configured clip bounds")
        if self.output_width % 2 or self.output_height % 2:
            raise ValueError("video output dimensions must be divisible by two")
        if self.opportunity_min_duration_seconds > self.opportunity_max_duration_seconds:
            raise ValueError("opportunity minimum duration must not exceed maximum duration")
        if self.analysis_motion_low_threshold > self.analysis_motion_high_threshold:
            raise ValueError("motion low threshold must not exceed high threshold")
        opportunity_weights = (
            self.opportunity_speech_weight,
            self.opportunity_motion_weight,
            self.opportunity_scene_weight,
            self.opportunity_transcript_weight,
            self.opportunity_ocr_weight,
            self.opportunity_audio_weight,
            self.opportunity_silence_weight,
            self.opportunity_event_weight,
            self.opportunity_visual_weight,
        )
        if not any(opportunity_weights):
            raise ValueError("at least one opportunity score weight must be positive")
        if max(opportunity_weights) > sum(opportunity_weights) * 0.4:
            raise ValueError("no single opportunity score weight may dominate the ranking")
        if not (
            self.storage_warning_threshold_percent
            < self.storage_critical_threshold_percent
            < self.storage_emergency_threshold_percent
        ):
            raise ValueError("storage warning, critical, and emergency thresholds must increase")
        if self.preview_enabled and not self.preview_public_base_url:
            raise ValueError("enabled previews require a public preview base URL")
        hardened_mode = self.deployment_mode in {
            DeploymentMode.IP_BOOTSTRAP,
            DeploymentMode.PRODUCTION,
        }
        if self.environment is Environment.PRODUCTION and not hardened_mode:
            raise ValueError("production environment requires an explicit production deployment mode")
        if hardened_mode:
            if self.enable_development_actor:
                raise ValueError("development actor must be disabled in hardened deployment modes")
            if self.api_secret == "change-me-development-only" or len(self.api_secret) < 32:
                raise ValueError(
                    "hardened deployment API secret must be a non-default value of at least 32 chars"
                )
            if self.database_url.startswith("sqlite"):
                raise ValueError("hardened deployment requires PostgreSQL")
            if self.storage_provider != "local":
                raise ValueError("hardened deployment storage provider is not configured")
            if self.preview_hashing_secret == "change-me-development-preview-secret" or len(self.preview_hashing_secret) < 32:
                raise ValueError("hardened deployments require a strong non-default preview secret")
            if not self.trusted_hosts or "*" in self.trusted_hosts:
                raise ValueError("hardened deployments require explicit trusted hosts")
            if "*" in self.cors_allowed_origins:
                raise ValueError("hardened deployments forbid a wildcard CORS origin")
            if self.discord_enabled and not self.discord_bot_token:
                raise ValueError("hardened deployment Discord service requires a bot token")
            if any(
                marker in self.database_url.lower()
                for marker in (":viralforge@", ":password@", ":change-me@", ":replace_", ":example@")
            ):
                raise ValueError("hardened deployment database URL contains a placeholder password")
        if self.deployment_mode is DeploymentMode.PRODUCTION:
            for name, value in (
                ("public base URL", self.public_base_url),
                ("preview public base URL", self.preview_public_base_url),
                ("OAuth callback base URL", self.oauth_callback_base_url),
            ):
                parsed = urlsplit(value)
                if parsed.scheme != "https" or not parsed.netloc:
                    raise ValueError(f"production {name} requires an HTTPS URL")
        if self.tiktok_application_review_state not in {"DEVELOPMENT", "UNAUDITED", "AUDITED"}:
            raise ValueError("TikTok application review state must be DEVELOPMENT, UNAUDITED, or AUDITED")
        if self.tiktok_default_mode not in {"DRAFT_UPLOAD", "DIRECT_POST"}:
            raise ValueError("TikTok default mode must be DRAFT_UPLOAD or DIRECT_POST")
        if self.tiktok_enabled:
            if not self.tiktok_client_id:
                raise ValueError("TikTok enabled requires a client key")
            if not self.tiktok_client_secret_credential_reference:
                raise ValueError("TikTok enabled requires a client-secret credential reference")
            if not self.tiktok_oauth_state_secret or len(self.tiktok_oauth_state_secret) < 32:
                raise ValueError("TikTok enabled requires a strong OAuth state secret")
            if self.credential_store_backend not in {"env", "encrypted_file"}:
                raise ValueError("credential store backend must be env or encrypted_file")
            if self.credential_store_backend == "encrypted_file" and not self.credential_store_master_key_reference:
                raise ValueError("encrypted_file credential storage requires a master-key reference")
        if self.tiktok_public_direct_post_enabled and self.tiktok_application_review_state != "AUDITED":
            raise ValueError("public TikTok Direct Post requires an AUDITED TikTok application")
        if self.deployment_mode is DeploymentMode.IP_BOOTSTRAP:
            if self.environment is not Environment.PRODUCTION:
                raise ValueError("ip_bootstrap requires the production environment")
            try:
                public_ip = str(ip_address(self.public_host))
            except ValueError as error:
                raise ValueError("ip_bootstrap requires a literal public VPS IP") from error
            if public_ip not in self.trusted_host_list():
                raise ValueError("ip_bootstrap trusted hosts must contain the exact public VPS IP")
            for name, value in (
                ("API base URL", self.api_base_url),
                ("public base URL", self.public_base_url),
                ("preview public base URL", self.preview_public_base_url),
            ):
                parsed = urlsplit(value)
                if (
                    parsed.scheme != "http"
                    or parsed.hostname != public_ip
                    or (parsed.port or 80) != self.ip_bootstrap_port
                ):
                    raise ValueError(
                        "ip_bootstrap "
                        f"{name} must be HTTP for the exact public VPS IP and configured port"
                    )
            if self.publishing_enabled or self.publishing_youtube_enabled:
                raise ValueError("ip_bootstrap forbids public publishing")
            if self.youtube_oauth_enabled or self.tiktok_enabled:
                raise ValueError("ip_bootstrap forbids OAuth account connections")
        return self

    def trusted_host_list(self) -> list[str]:
        return [item.strip() for item in self.trusted_hosts.split(",") if item.strip()]

    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_allowed_origins.split(",") if item.strip()]

    def oauth_callback_url(self, provider: str) -> str:
        if provider not in {"youtube", "tiktok"}:
            raise ValueError("unsupported OAuth callback provider")
        if self.deployment_mode is DeploymentMode.IP_BOOTSTRAP:
            raise ValueError("A trusted HTTPS hostname is required before this feature can be enabled.")
        return f"{self.oauth_callback_base_url.rstrip('/')}/api/v1/oauth/{provider}/callback"

    def require_trusted_https_feature(self) -> None:
        if self.deployment_mode is DeploymentMode.IP_BOOTSTRAP:
            raise ValueError("A trusted HTTPS hostname is required before this feature can be enabled.")


@lru_cache
def get_settings() -> Settings:
    return Settings()
