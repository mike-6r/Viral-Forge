# Multi-Brand Foundation Implementation Report

## Outcome

Implemented workspace and brand tenancy without enabling publishing or changing the existing publishing boundary.

## Data model

- `Workspace` contains multiple `Brand` records.
- `BrandMembership` provides per-brand access and a persisted default brand selection.
- `ContentProfile` persists niche, discovery categories/keywords, preferred providers, clip bounds, opportunity weights/reference, tone/style, hashtag and branding rules, review requirements, maximum posts/day, platforms, language, and timezone.
- `SourceAccount` stores only public source identity and provider metadata.
- `DestinationAccount` stores provider/account references and a credential reference ID only. Account metadata rejects credential-shaped keys; raw credentials are not stored or logged.
- `BrandingProfile`, `ReviewPolicy`, and `PostingPolicy` establish one configurable record per brand for future downstream use. They do not publish or schedule content.

## Attribution and isolation

The following operational records now carry a non-null `brand_id`: discovery source/media/run, production project/source/clip, analysis, opportunity run/opportunity, content package, posting queue item, and audit event. Downstream records inherit the brand from their parent project or source at creation.

API project, discovery, queue, clip, and content-package access is scoped through the active/default brand or an explicit authorized brand. Discord now has `/viralforge brands` with a selector that persists the active brand. Its control-center counts, projects, queue, and review inbox are filtered to that brand.

## Migration strategy

Migration `0016_multi_brand_foundation` creates a `Legacy Workspace` and `Legacy Brand`, creates a legacy content profile and memberships for existing users, then backfills existing operational records before making their brand fields non-null. Existing records remain present.

## Verification

- Full test suite: **93 passed**.
- Includes cross-brand API isolation, content-profile/account API, legacy migration cycle, and brand inheritance coverage.
- Ruff: passed.
- mypy: passed for 65 application source files.
- Alembic/ORM schema drift: no drift detected.
- Docker images rebuilt successfully.
- PostgreSQL migration applied successfully.
- Live database verification: 1 legacy workspace, 1 legacy brand, and zero unbranded projects, clips, content packages, or discovery sources.
- API health and Celery ping passed.
- Discord bot rebuilt, running, gateway-connected, and uses the brand-aware slash-command setup.

## Files changed

- `app/brands/__init__.py`
- `app/brands/models.py`
- `app/brands/service.py`
- `alembic/versions/0016_multi_brand_foundation.py`
- `app/production/models.py`
- `app/production/service.py`
- `app/analysis/models.py`
- `app/analysis/service.py`
- `app/opportunities/models.py`
- `app/opportunities/service.py`
- `app/content_packages/models.py`
- `app/content_packages/service.py`
- `app/discovery/models.py`
- `app/discovery/service.py`
- `app/audit/models.py`
- `app/api.py`
- `app/discord_bot.py`
- `alembic/env.py`
- `scripts/schema_drift.py`
- `tests/conftest.py`
- `tests/test_multi_brand.py`

## Remaining issues

None. Publishing remains unimplemented and was not invoked.
