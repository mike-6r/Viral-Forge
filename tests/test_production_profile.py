from pathlib import Path

from app.worker import celery_app


def test_production_profile_is_private_and_has_all_operational_services():
    compose = Path("docker-compose.prod.yml").read_text(encoding="utf-8")
    assert "caddy:" in compose
    assert "scheduler:" in compose
    assert "internal: true" in compose
    assert '"80:80", "443:443"' in compose
    assert "ports: !reset []" in compose
    assert 'env_file: !override ["${VIRALFORGE_PRODUCTION_ENV_FILE:-.env.production}"]' in compose
    assert "service_completed_successfully" in compose
    assert "/viralforge-data" in compose
    assert "egress:" in compose
    assert "networks: [private, egress]" in compose
    caddy_block = compose.split("  caddy:", 1)[1].split("volumes:", 1)[0]
    assert "no-new-privileges" not in caddy_block
    assert "cap_drop" not in caddy_block


def test_scheduler_only_dispatches_bounded_existing_tasks():
    tasks = celery_app.conf.beat_schedule
    assert {entry["task"] for entry in tasks.values()} >= {
        "viralforge.scheduler_heartbeat",
        "viralforge.cleanup_expired_media",
        "viralforge.refresh_published_analytics",
        "viralforge.execute_due_publish_requests",
        "viralforge.discovery_poll_due_sources",
    }


def test_backup_verification_is_disposable_and_never_targets_active_database():
    script = Path("scripts/production/restore-verify.sh").read_text(encoding="utf-8")
    assert "viralforge_restore_verify" in script
    assert "--if-exists" in script
    assert '"$verify_db"' in script
    assert '"${POSTGRES_DB:-viralforge}"' not in script


def test_monitor_deduplicates_alerts_and_detects_stale_scheduler():
    script = Path("scripts/production/monitor.sh").read_text(encoding="utf-8")
    assert "scheduler_stale" in script
    assert "VIRALFORGE_SCHEDULER_STALE_MINUTES" in script
    assert "VIRALFORGE_ALERT_WEBHOOK_URL" in script
    assert 'test "$current" != "$previous"' in script
    assert "celery -A app.worker:celery_app inspect ping" in script


def test_ip_bootstrap_profile_is_explicit_http_only_and_keeps_api_private():
    compose = Path("docker-compose.ip-bootstrap.yml").read_text(encoding="utf-8")
    caddy = Path("Caddyfile.ip-bootstrap.example").read_text(encoding="utf-8")
    environment = Path(".env.ip-bootstrap.example").read_text(encoding="utf-8")
    assert 'ports: !override ["${VIRALFORGE_IP_BOOTSTRAP_PORT:-8081}:80"]' in compose
    assert 'env_file: !override ["${VIRALFORGE_IP_BOOTSTRAP_ENV_FILE:-.env.ip-bootstrap}"]' in compose
    assert "http://{$VIRALFORGE_PUBLIC_IP}" in caddy
    assert "file_server" not in caddy
    assert "VIRALFORGE_DEPLOYMENT_MODE=ip_bootstrap" in environment
    assert "VIRALFORGE_PUBLISHING_ENABLED=false" in environment
    assert "VIRALFORGE_YOUTUBE_OAUTH_ENABLED=false" in environment
