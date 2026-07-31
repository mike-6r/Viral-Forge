# Multi-Platform Public Video Discovery Engine

## Outcome

ViralForge now has a separate, review-first discovery domain. It finds candidates only through configured public providers, persists every run and item, suppresses exact duplicates, scores relevance, and requires review before it creates a production project. It does not download or render discovered items automatically, publish content, remove watermarks, scrape logged-in accounts, or bypass platform controls.

## Architecture and providers

`app.discovery.providers` defines a common provider interface with configuration validation, polling, cursor support, capabilities, and typed provider errors. Implemented providers are:

- YouTube Data API: configured channel polling or keyword search, bounded result count, public API key required.
- RSS/Atom: safely fetches explicitly configured public feeds through the existing safe outbound client and parses entries with the existing hardened parser.

TikTok, Instagram, Facebook, and X adapters are explicitly disabled and state the official API or lawful authenticated integration required. No unofficial login scraping, cookies, browser-account automation, or rate-limit evasion exists. Generic arbitrary webpage discovery remains disabled until an explicit safe parser is configured.

## Models and migration

Migration `0011_discovery_engine` adds:

- `DiscoverySource`: configured agency/provider account/feed/page, safe polling state, trust flag, and public configuration.
- `DiscoveredMedia`: normalized candidate, attribution, relevance/duplicate/review state, and optional linked production project.
- `DiscoveryRun`: durable run status, cursor, counts, errors, and metrics.

`0011_discovery_engine` is applied to PostgreSQL. The SQLite upgrade/downgrade/re-upgrade test and schema-drift check pass.

## Flow

1. An administrator configures an enabled public discovery source.
2. A bounded worker task runs an eligible source with provider failure isolation and jittered next-poll scheduling.
3. Results are normalized, exact duplicates are skipped, probable duplicates are retained for review, and relevance is scored from agency, trusted status, configured keywords/categories, recency, usable media, and excluded terms.
4. Qualified items become `NEEDS_REVIEW`; lower relevance and duplicate states remain visible.
5. An authorized reviewer approves or rejects a candidate. Approval is versioned/idempotent, creates exactly one existing `ProductionProject`, and hands off to the existing source-quality resolver. Download and clipping remain separate explicit actions.

## Scheduler, observability, and configuration

`viralforge.discovery_poll_due_sources` is a Celery task—not an API-loop. It is disabled by default and processes a bounded number of due sources per tick. Provider errors are contained to their run and use exponential-backoff scheduling. Audit events record discovery, run completion, approval, and rejection without credentials, cookies, authorization headers, or signed URLs.

`.env.example` adds discovery enablement, scheduler, concurrency, timeout/retry/backoff, result limits, relevance/review thresholds, source trust automation guard, age/duration/duplicate thresholds, review channel, and relevance weight path. `config/discovery_relevance_weights.yml` controls scoring. The existing official-source registry was extended with disabled example discovery metadata only.

## Discord and API

Discord has `/discovery status`, `/discovery queue`, `/discovery approve`, and `/discovery reject`. The queue renders authorized review controls for approve, reject, and source URL; stale actions are rejected by review versioning.

API endpoints provide source creation/listing, a bounded single-source run, discovery run/media listing and inspection, approval/rejection, and provider capability health at `/api/v1/discovery/*`. No provider credential is returned.

## Verification

- Full suite: passed (including mocked discovery run, duplicate suppression, relevance, failure isolation, stale rejection, and idempotent production-project approval).
- Ruff: passed.
- mypy: passed for 53 modules.
- Schema drift: passed.
- Docker rebuild: passed.
- PostgreSQL: `0011_discovery_engine (head)`.
- API `/ready`: passed.
- Celery worker: running; `viralforge.discovery_poll_due_sources` registered.
- Discord bot: reconnected to the Gateway.

## Compliance limitation

No real YouTube channel was polled because `config/official_sources.yml` contains only clearly disabled example accounts and no enabled real agency channel is configured. This is an intentional safety boundary, not a platform failure. To perform a real safe poll, add a verified public agency channel ID to the registry/configuration, enable that discovery source, and ensure a valid `YOUTUBE_API_KEY` is configured. The engine does not broad-crawl or silently substitute channels.

## Files changed

- `app/discovery/*`, `app/worker.py`, `app/api.py`, `app/discord_bot.py`
- `app/common/config.py`, `scripts/schema_drift.py`
- `alembic/versions/0011_discovery_engine.py`
- `config/official_sources.yml`, `config/discovery_relevance_weights.yml`, `.env.example`
- `tests/test_discovery.py`

## Next recommended milestone

Configure a small verified set of real public agency sources and conduct an operator-observed, rate-limited discovery review trial. Add any further provider only through its official API or a documented lawful public access method.
