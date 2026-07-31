# Analytics Feedback Implementation Report

## Outcome

Implemented a read-only analytics and feedback foundation for successfully published YouTube posts. It produces stored evidence and operator-facing recommendations only; it does not change production, scheduling, content packages, or brand settings.

## Implemented

- `AnalyticsProvider` boundary with the official YouTube Analytics API v2 provider as the first and only provider.
- Brand-scoped periodic `PostAnalyticsSnapshot` records linked to a successful `PublishRequest` and rendered clip.
- Normalized nullable metrics: views, watch time, average view duration, derived retention percentage, likes, comments, shares, saves, followers/subscribers gained, clicks, and platform revenue/currency.
- Unavailable metrics remain `null`; they are never represented as fabricated zeroes. Revenue is accepted only from an official provider path or an explicit operator import.
- Bounded platform-specific `raw_metadata`, storing only safe provider response descriptors rather than credentials or unbounded payloads.
- Operator import API for known metrics and explicit feedback-label API for qualitative observations.
- Brand dashboard aggregations for post/source, topic/category, clip duration band, primary hook, and posting-time performance. Recommendations are observations from recorded data only.
- Discord `/viralforge analytics` summary for the selected brand.
- Bounded Celery `viralforge.refresh_published_analytics` task. It is disabled by default and will not call YouTube unless both analytics settings are explicitly enabled.
- No automated production-setting changes and no automatic publishing action.

## Files changed

- `app/analytics/models.py`
- `app/analytics/service.py`
- `app/api.py`
- `app/worker.py`
- `app/discord_bot.py`
- `app/common/config.py`
- `alembic/versions/0018_analytics_feedback.py`
- `.env.example`
- `tests/test_analytics_feedback.py`

## Verification

- Focused analytics and migration tests: passed.
- Full test suite: 97 passed.
- Ruff: passed.
- mypy: passed.
- The API/worker Docker images were rebuilt, and PostgreSQL was upgraded to `0018_analytics_feedback`.

## Live API status

The current configuration keeps `VIRALFORGE_ANALYTICS_ENABLED=false` and `VIRALFORGE_ANALYTICS_YOUTUBE_ENABLED=false`. No YouTube query was made and no settings were changed automatically. To enable a later read-only refresh, an operator must explicitly enable both flags and provide a valid externally referenced YouTube OAuth credential with analytics scope on the destination account.
