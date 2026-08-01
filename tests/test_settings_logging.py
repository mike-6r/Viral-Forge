import logging

import pytest
from pydantic import ValidationError

from app.common.config import Settings
from app.common.logging import RedactingLogFilter, redact
from app.publishing.service import PublishingError, _require_trusted_https_feature


def test_production_requires_real_secret_and_no_development_actor():
    with pytest.raises(ValidationError):
        Settings(
            environment="production",
            enable_development_actor=True,
            api_secret="x" * 40,
            database_url="postgresql+psycopg://x",
        )


def test_log_redaction():
    assert redact({"access_token": "do-not-log", "nested": {"password": "do-not-log"}}) == {
        "access_token": "[REDACTED]",
        "nested": {"password": "[REDACTED]"},
    }


def test_access_log_filter_redacts_preview_query_token():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s" %s',
        ("127.0.0.1", "/preview/id?token=private-capability", 200),
        None,
    )
    assert RedactingLogFilter().filter(record)
    assert "private-capability" not in record.getMessage()


def production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "production",
        "deployment_mode": "production",
        "enable_development_actor": False,
        "api_secret": "a" * 40,
        "preview_hashing_secret": "p" * 40,
        "database_url": "postgresql+psycopg://viralforge:correct-horse-battery@postgres:5432/viralforge",
        "public_base_url": "https://app.example.test",
        "public_host": "app.example.test",
        "preview_public_base_url": "https://app.example.test",
        "oauth_callback_base_url": "https://app.example.test",
        "trusted_hosts": "app.example.test",
    }
    values.update(overrides)
    return Settings(**values)


def test_production_requires_trusted_host_and_secure_oauth_callback():
    with pytest.raises(ValidationError):
        production_settings(trusted_hosts="")
    with pytest.raises(ValidationError):
        production_settings(oauth_callback_base_url="http://app.example.test")
    assert production_settings().oauth_callback_url("youtube") == (
        "https://app.example.test/api/v1/oauth/youtube/callback"
    )


def test_production_rejects_wildcard_cors_and_placeholder_database_password():
    with pytest.raises(ValidationError):
        production_settings(cors_allowed_origins="*")
    with pytest.raises(ValidationError):
        production_settings(database_url="postgresql+psycopg://viralforge:viralforge@postgres/db")


def ip_bootstrap_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "production",
        "deployment_mode": "ip_bootstrap",
        "enable_development_actor": False,
        "api_secret": "a" * 40,
        "preview_hashing_secret": "p" * 40,
        "database_url": "postgresql+psycopg://viralforge:correct-horse-battery@postgres:5432/viralforge",
        "public_host": "198.51.100.10",
        "ip_bootstrap_port": 8081,
        "api_base_url": "http://198.51.100.10:8081",
        "public_base_url": "http://198.51.100.10:8081",
        "preview_public_base_url": "http://198.51.100.10:8081",
        "oauth_callback_base_url": "http://198.51.100.10:8081",
        "trusted_hosts": "198.51.100.10,localhost,api",
        "publishing_enabled": False,
        "publishing_youtube_enabled": False,
        "youtube_oauth_enabled": False,
        "tiktok_enabled": False,
    }
    values.update(overrides)
    return Settings(**values)


def test_ip_bootstrap_allows_only_exact_http_ip_with_hardened_settings():
    settings = ip_bootstrap_settings()
    assert settings.deployment_mode == "ip_bootstrap"
    assert settings.preview_public_base_url == "http://198.51.100.10:8081"
    with pytest.raises(ValidationError):
        ip_bootstrap_settings(trusted_hosts="198.51.100.11")
    with pytest.raises(ValidationError):
        ip_bootstrap_settings(preview_public_base_url="http://example.test")
    with pytest.raises(ValidationError):
        ip_bootstrap_settings(preview_public_base_url="http://198.51.100.10:8082")
    with pytest.raises(ValidationError):
        ip_bootstrap_settings(cors_allowed_origins="*")
    with pytest.raises(ValidationError):
        ip_bootstrap_settings(enable_development_actor=True)
    with pytest.raises(ValidationError):
        ip_bootstrap_settings(api_secret="short")


def test_ip_bootstrap_blocks_oauth_and_publishing_but_production_stays_https_only():
    settings = ip_bootstrap_settings()
    with pytest.raises(ValueError, match="trusted HTTPS hostname"):
        settings.oauth_callback_url("youtube")
    with pytest.raises(ValueError, match="trusted HTTPS hostname"):
        settings.require_trusted_https_feature()
    with pytest.raises(PublishingError, match="trusted HTTPS hostname"):
        _require_trusted_https_feature(settings)
    with pytest.raises(ValidationError):
        ip_bootstrap_settings(youtube_oauth_enabled=True)
    with pytest.raises(ValidationError):
        ip_bootstrap_settings(tiktok_enabled=True)
    with pytest.raises(ValidationError):
        ip_bootstrap_settings(publishing_enabled=True)
    with pytest.raises(ValidationError):
        production_settings(preview_public_base_url="http://app.example.test")
