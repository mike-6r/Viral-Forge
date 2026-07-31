# ViralForge Milestone 1 Audit Findings

This register records only issues independently confirmed from implementation or runtime behavior.

| ID | Severity | Affected files | Requirement and evidence | Recommended correction | Status |
|---|---|---|---|---|---|
| VF-AUD-001 | HIGH | `app/content/lifecycle.py` | Confirmed at runtime: a manual `DENIED` rights record with `disposition=APPROVED` reached `APPROVED`. | Require an eligible rights state, current validity, and reject blocking manual assessments. | Corrected; 5 parametrized/expiry regression checks pass. |
| VF-AUD-002 | HIGH | `app/content/lifecycle.py` | A single manual moderation approval permitted release when another manual rejection existed. | Reject release when any manual moderation rejection is present; require a valid manual approval. | Corrected; regression check passes. |
| VF-AUD-003 | HIGH | `app/accounts/auth.py`, `app/api.py` | Every development header UUID was silently assigned `ADMIN`, regardless of persistence or role. | Resolve development actors from persisted users and roles through the request session; reject unknown/inactive actors. | Corrected; API tests now use a persisted role-bearing actor and missing actors return 401 in live verification. |
| VF-AUD-004 | HIGH | `alembic/versions/0001_foundation.py` | The first migration still calls mutable `Base.metadata.create_all/drop_all`, so historical schema depends on current application models. | Replace the initial migration with explicit DDL before any release. | Open — corrective `0002_harden_foundation` supports existing deployments, but does not eliminate this historical reproducibility risk. |
| VF-AUD-005 | MEDIUM | `app/content/models.py`, `app/content/lifecycle.py` | Content transitions lacked optimistic locking. | Add an SQLAlchemy version column and map stale writes to a conflict. | Corrected in models/API; migration `0002` adds `version_id` for existing databases. |
| VF-AUD-006 | MEDIUM | `app/common/logging.py`, `app/api.py` | Request/correlation IDs were not bound to log context and redaction did not cover URL query values. | Bind request context; redact sensitive URL query values recursively. | Partially corrected: request/correlation context and URL-query redaction added. Service/environment/actor/entity/job event binding remains open. |
| VF-AUD-007 | MEDIUM | `app/api.py` | Route handlers own source normalization, persistence orchestration, audit creation, and transaction management. | Move create/transition application operations to narrowly scoped services. | Open — architectural condition for Milestone 2. |
| VF-AUD-008 | MEDIUM | `app/ranking/models.py`, `app/content/models.py` | Ranking scores/progress values had no database constraints. | Add check constraints and a corrective migration. | Partially corrected: confidence and job bounds are modeled and PostgreSQL constraints are included in `0002`; score-component bounds remain undefined pending a documented scoring scale. |
| VF-AUD-009 | LOW | `app/worker.py`, `app/content/models.py` | Full job recovery fields existed only on `ProcessingJob`. | Extend publishing jobs with the shared recovery fields and migration. | Corrected by `0002_harden_foundation`. |
| VF-AUD-010 | LOW | `app/api.py` | Request validation used FastAPI's default response shape. | Add a request-validation handler. | Corrected; malformed UUID returns the standard error envelope with 422. |

Findings are updated with verification evidence after correction.
