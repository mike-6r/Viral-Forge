# Temporary IP bootstrap deployment

Use this profile only while no trusted DNS hostname is available. It serves
`http://PUBLIC_VPS_IP:8081` for operator testing; HTTP is not encrypted. It does
not weaken the normal HTTPS production profile and must be replaced by it once
a hostname is available.

## VPS setup

1. Provision Ubuntu LTS and connect with SSH keys: `ssh USER@PUBLIC_VPS_IP`.
2. Install Git and Docker Engine/Compose from their official Ubuntu guidance.
   Use a non-root sudo user; see `VPS_SECURITY_CHECKLIST.md` for SSH, patches,
   time synchronization, and Docker-group guidance.
3. Clone the repository: `git clone REPOSITORY_URL ViralForge && cd ViralForge`.
4. Create configuration without replacing local or HTTPS-production files:
   `cp .env.ip-bootstrap.example .env.ip-bootstrap`, replace every placeholder,
   then run `chmod 600 .env.ip-bootstrap`.
5. Restrict the temporary HTTP endpoint. The default bootstrap port is `8081`,
   avoiding a collision with an existing site on ports 80/443. Recommended
   Option A is a VPS-provider firewall/security-group rule allowing TCP 8081
   only from `OPERATOR_PUBLIC_IP`.
   Also configure host UFW for SSH and use Docker's `DOCKER-USER` chain because
   Docker-published ports can bypass ordinary UFW rules:

   ```sh
   sudo ufw allow OpenSSH
   sudo ufw enable
   OPERATOR_PUBLIC_IP=REPLACE_WITH_YOUR_HOME_PUBLIC_IP
   sudo iptables -I DOCKER-USER 1 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
   sudo iptables -I DOCKER-USER 2 -i eth0 -p tcp --dport 8081 -s "$OPERATOR_PUBLIC_IP" -j ACCEPT
   sudo iptables -I DOCKER-USER 3 -i eth0 -p tcp --dport 8081 -j DROP
   ```

   Persist the `DOCKER-USER` rules using your Ubuntu firewall-management
   procedure or apply the equivalent provider-firewall rule. Option B, only when
   a reviewer must open a preview, permits TCP 8081 publicly at the provider and
   omits the two port-8081 `DOCKER-USER` rules. Keep preview TTL at 900 seconds or
   less, maximum access count low, use random grants, avoid sensitive media, and
   revoke grants immediately after review. In both options, do not open 5432,
   6379, or 8000. API, database, Redis,
   worker, scheduler, Discord, and storage remain private Docker services.
6. Make the deployment script executable and deploy:

   ```sh
   chmod 700 scripts/production/deploy-ip-bootstrap.sh
   ./scripts/production/deploy-ip-bootstrap.sh
   ```

   It validates the combined Compose profile, builds images, checks/applies
   Alembic migrations, starts services, checks API health/readiness, and pings
   the worker. Check scheduler/Discord explicitly with:

   ```sh
   docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.ip-bootstrap.yml --env-file .env.ip-bootstrap logs --tail=100 scheduler discord
   ```

7. Verify operator HTTP access: `curl --fail http://PUBLIC_VPS_IP:8081/health` and
   `curl --fail http://PUBLIC_VPS_IP:8081/ready`. Generate a normal private preview
   from the review workflow, test full playback plus `Range: bytes=0-1023`, and
   run cleanup dry-run from the worker. Confirm media/model markers survive an
   API/worker recreation.

## Explicit restrictions

`ip_bootstrap` requires PostgreSQL, strong API/preview secrets, trusted hosts
containing the exact public IP plus `localhost,api` for private Docker health
checks, no wildcard CORS, and disabled development actors. It blocks OAuth
callback construction, new destination-account connections, publish requests,
and confirmation with: `A trusted HTTPS hostname is required before this feature
can be enabled.` TikTok, YouTube OAuth, and all publishing flags are rejected at
startup if enabled. Discovery, local download, analysis, clipping, private
preview/review, cleanup, manual metrics, and Discord operations remain usable.

Do not put credentials or sensitive previews on a public HTTP connection. Caddy
is a reverse proxy only: it has no `file_server`, so all preview media continues
through the token-validated API stream and supports Range requests.

## Switch to normal HTTPS production

After DNS for a trusted hostname resolves, preserve the Docker volumes, create
`.env.production` from `.env.production.example`, set
`VIRALFORGE_DEPLOYMENT_MODE=production`, configure the HTTPS hostname and exact
YouTube callback URI, then run:

```sh
docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.ip-bootstrap.yml --env-file .env.ip-bootstrap down
chmod 600 .env.production
./scripts/production/deploy.sh
```

Remove the temporary UFW port-80 rule and allow 80/443 only as required for
Caddy's certificate issuance and normal HTTPS operation. Do not migrate, delete
volumes, or enable publishing merely because the mode changes.

## Stop local Windows services only after VPS verification

Run these from the repository directory in PowerShell only after VPS health,
Discord, preview/range, persistence, and cleanup checks pass:

```powershell
docker compose down
docker rm -f viralforge-discord-1 2>$null
docker ps --filter "name=viralforge" --format "table {{.Names}}\t{{.Status}}"
```

`down` without `-v` preserves local named volumes and source files. Optionally
disable Docker Desktop's startup behavior from Docker Desktop Settings after the
container list is empty; do not delete volumes.
