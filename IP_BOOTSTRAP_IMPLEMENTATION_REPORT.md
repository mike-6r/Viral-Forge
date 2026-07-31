# IP bootstrap implementation report

## Scope

Added an explicitly selected `VIRALFORGE_DEPLOYMENT_MODE=ip_bootstrap` profile
for temporary `http://PUBLIC_VPS_IP` operator testing. Normal production remains
an explicit `production` mode and still requires HTTPS for public, preview, and
OAuth callback URLs.

## Controls

- Bootstrap requires `VIRALFORGE_ENVIRONMENT=production`, PostgreSQL, strong
  API and preview secrets, no placeholder database password, disabled
  development actor, explicit non-wildcard trusted hosts containing the exact
  public IP, and non-wildcard CORS.
- API, public, and preview URL values must be exact HTTP URLs for that IP.
- Publishing, YouTube OAuth, and TikTok are startup-blocked. Both API and
  shared publishing-service attempts to create/verify destination connections
  or create/confirm publishing requests return the required trusted-HTTPS error.
- `docker-compose.ip-bootstrap.yml` overrides Caddy to publish port 80 only;
  it adds no API/database/Redis host ports. `Caddyfile.ip-bootstrap.example`
  proxies only to FastAPI, has safe headers and streaming timeouts, and no
  static storage mapping.
- `.env.ip-bootstrap.example` contains placeholders only and sets conservative
  preview TTL/access limits with publishing/OAuth/analytics refresh disabled.

## Operator status

No VPS credentials, public IPv4 address, DNS record, or live deployment was
provided. The implementation therefore includes exact owner instructions in
`IP_BOOTSTRAP_DEPLOYMENT_GUIDE.md` but does not claim a real VPS deployment.

## Verification

- `python -m pytest -q`: 112 passed (two existing upstream deprecation warnings).
- `python -m ruff check .`: passed.
- `python -m mypy app`: passed, 71 source files.
- `python scripts/schema_drift.py`: no schema drift detected.
- Disposable PostgreSQL Compose migration cycle: upgraded cleanly to
  `0021_media_preview`; `alembic check` reported no new upgrade operations.
- Combined base/production/IP-bootstrap Compose configuration: passed. Rendered
  Caddy ports contain exactly `80:80`; the API, PostgreSQL, and Redis have no
  host-port mappings.
- `Caddyfile.ip-bootstrap.example`: passed `caddy validate`; Caddy explicitly
  reports that automatic HTTPS is not applied, as intended for temporary HTTP.

No VPS access was available, so public HTTP, Discord, preview/range playback,
or migration execution were not claimed as live VPS tests.
