# Local development

Requires Python 3.12 and Docker Desktop (or Docker Engine). Copy `.env.example` to `.env`. On Windows use `./scripts/dev.ps1 up`; on Linux/macOS use `make up`. In another terminal run `./scripts/dev.ps1 migrate` or `make migrate`.

Commands: `dev.ps1 test|lint|typecheck|down|reset`. Linux equivalents are `make test|lint|typecheck|down|reset`. Reset destroys the named Docker volume; use only for local development.

Use `./scripts/dev.ps1 build|up|status|logs|worker-logs|migrate|downgrade|down`. `reset` requires `-Force` because it deletes local Docker data. Migrations, not application startup, create the schema. See [migration policy](MIGRATION_POLICY.md).

Worker recovery plan: workers update job heartbeats; a future periodic task will mark jobs with expired heartbeats `STALE`, then retry only idempotent jobs within their attempt cap. The current worker includes only previews and does not manipulate media or publish.
# Local upload storage

Set `VIRALFORGE_LOCAL_STORAGE_ROOT` to a directory outside the repository. The default is `../viralforge-data/uploads`, with separate `tmp` and `assets` directories. Do not expose that directory as static web content.
