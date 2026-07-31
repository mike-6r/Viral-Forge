param(
  [Parameter(Mandatory=$true)][ValidateSet('up','down','build','reset','migrate','downgrade','test','lint','typecheck','logs','worker-logs','status','seed')][string]$Command,
  [switch]$Force
)
switch ($Command) {
  'up' { docker compose up --build }
  'down' { docker compose down }
  'build' { docker compose build }
  'reset' {
    if (-not $Force) { throw 'Reset deletes the local Docker volume. Re-run with -Force to continue.' }
    docker compose down -v
    docker compose up --build
  }
  'migrate' { docker compose run --rm api alembic upgrade head }
  'downgrade' { docker compose run --rm api alembic downgrade base }
  'test' { python -m pytest }
  'lint' { python -m ruff check . }
  'typecheck' { python -m mypy app }
  'logs' { docker compose logs -f }
  'worker-logs' { docker compose logs -f worker }
  'status' { docker compose ps }
  'seed' { Write-Host 'No seed data is created automatically in Milestone 1.' }
}
