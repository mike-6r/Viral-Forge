# Architecture

ViralForge is a modular monolith. Domain modules own concepts and rules; FastAPI routes adapt HTTP to services; SQLAlchemy implements persistence; Celery executes only operational demonstrations. Provider contracts live under `app/common/adapters.py` and intentionally have no concrete integrations.

Modules: `accounts`, `sources`, `content`, `rights`, `moderation`, `ranking`, `processing`, `review`, `publishing`, `analytics`, `audit`, and `common`. This preserves boundaries without premature services.

Alembic is the schema authority. Released migrations are immutable; the unreleased initial migration was corrected before shared deployment because it previously depended on live ORM metadata.
# Upload boundary

Manual upload orchestration is part of the ingestion module. Routes only adapt multipart input; upload policy, temporary storage, signature detection, duplicate handling, provenance, lifecycle, and audit are coordinated by `app.ingestion.upload`.
