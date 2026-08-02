from app.common.db import Base
from app.worker import (
    application_heartbeat,
    audit_cleanup_preview,
    celery_app,
    generate_clip_opportunities,
    process_accepted_source,
    render_approved_opportunity,
    run_video_analysis,
    stale_job_detection_preview,
)


def test_safe_worker_tasks_are_registered_and_explicit_previews():
    # The worker must load every table referenced by ProductionClip before a
    # renderer task configures SQLAlchemy's mapper graph.
    assert "clip_correction_plans" in Base.metadata.tables
    assert "viralforge.heartbeat" in celery_app.tasks
    assert application_heartbeat() == {"status": "ok", "service": "viralforge-worker"}
    assert stale_job_detection_preview()["status"] == "preview"
    assert "No records deleted" in audit_cleanup_preview()["message"]
    assert "viralforge.run_video_analysis" in celery_app.tasks
    assert run_video_analysis.name == "viralforge.run_video_analysis"
    assert "viralforge.generate_clip_opportunities" in celery_app.tasks
    assert generate_clip_opportunities.name == "viralforge.generate_clip_opportunities"
    assert "viralforge.process_accepted_source" in celery_app.tasks
    assert process_accepted_source.name == "viralforge.process_accepted_source"
    assert "viralforge.render_approved_opportunity" in celery_app.tasks
    assert render_approved_opportunity.name == "viralforge.render_approved_opportunity"
