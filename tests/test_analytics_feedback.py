from datetime import UTC, datetime

from app.analytics.service import NormalizedMetrics, add_feedback, dashboard, persist_snapshot
from app.publishing.models import PublishRequestStatus
from app.publishing.service import request_publish
from tests.conftest import DEV_ACTOR_ID
from tests.test_publishing_foundation import _ready_publish_context


def test_snapshot_feedback_and_recommendation_dashboard_are_read_only(session):  # type: ignore[no-untyped-def]
    brand, clip, package, destination = _ready_publish_context(session)
    request = request_publish(session, DEV_ACTOR_ID, clip, package, destination, "analytics-key-001", "MANUAL")
    request.status, request.remote_post_id = PublishRequestStatus.SUCCEEDED, "video123"
    session.commit()
    snapshot = persist_snapshot(session, request, NormalizedMetrics(views=100, watch_time_seconds=250.0, average_view_duration_seconds=12.5, retention_percentage=62.5, likes=10, raw_metadata={"reported_columns": ["views"]}), "OPERATOR_IMPORT", datetime.now(UTC))
    assert snapshot.saves is None
    assert snapshot.platform_revenue is None
    feedback = add_feedback(session, DEV_ACTOR_ID, request, "HOOK", "clear opening", "Operator observation")
    assert feedback.label == "HOOK"
    summary = dashboard(session, brand.id)
    assert summary["published_posts"] == 1
    assert summary["views"] == 100
    assert summary["recommendations"]
