# ViralForge production VPS deployment

This repository supplies a production profile; it does not deploy a VPS automatically. Use Ubuntu LTS, Docker Engine/Compose from Docker's official repository, a DNS A/AAAA record for `app.example.com`, and public firewall access only to TCP 80/443 (plus SSH from trusted networks).

1. Clone the repository as a non-root sudo user. Copy `.env.production.example` to `.env.production`, replace every placeholder, then `chmod 600 .env.production`.
2. Set `VIRALFORGE_PUBLIC_HOST`, public, preview, and OAuth callback URLs to the same HTTPS hostname. Configure the Caddyfile before starting services. Caddy obtains certificates after DNS resolves.
3. Make scripts executable: `chmod 700 scripts/production/*.sh`. Run `./scripts/production/deploy.sh`.
4. Verify `docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production ps`, HTTPS `/health`, `/ready`, Discord connection, and a private preview URL.

Only Caddy publishes ports. PostgreSQL, Redis, worker, scheduler, Discord, storage, and the API are private Docker-network services. No Caddy `root`/`file_server` mapping exists; previews stream exclusively through FastAPI token endpoints.

YouTube's later OAuth console callback URL is `https://app.example.com/api/v1/oauth/youtube/callback`. A future TikTok callback would be `https://app.example.com/api/v1/oauth/tiktok/callback`; do not register or enable TikTok here.

For 30–50 short clips, start with a 100 GB volume and alert at 75%; increase capacity for source-video retention and several brands. Media/model volumes survive container recreation; temporary media is intentionally excluded from backups.
