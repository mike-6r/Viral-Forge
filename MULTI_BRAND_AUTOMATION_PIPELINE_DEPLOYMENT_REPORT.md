# Multi-Brand Automation Pipeline Deployment Report

Date: 2026-08-01

## SSH result

The previous address was incorrect. The correct VPS address is `198.251.78.178`.

```text
TCP port 22: reachable
SSH handshake: successful; known ED25519 host key matched
Server authentication methods: publickey,password
Codex session authentication result: Permission denied (publickey,password)
SSH exit code: 255
```

Verbose SSH completed key exchange, verified the known server host key, and reached user authentication. This Codex session has neither an SSH-agent identity nor a local private key. No private-key material was read or printed.

## Git state

- Expected application commit: `5f88343` (`Automate approved opportunity rendering`)
- Local repository: pushed to `origin/main`
- Previous VPS commit: `ae2172d` (reported from the read-only operator inspection).
- Incoming application commits: `65aaa98`, `5f88343`, and `466fbeb`.
- Deployed VPS commit: still `ae2172d`; no pull was attempted from this Codex session.
- Known VPS working tree: modified `scripts/production/deploy-ip-bootstrap.sh`; untracked `.env.ip-bootstrap`, `.env.ip-bootstrap.before-smoke-test`, and `build/`.
- The deployment-script diff has not been inspected by this Codex session because authentication is unavailable. Those VPS-local files remain untouched.

## Backup and service rebuild

Not attempted. The required production backup must run on the VPS before a pull, and it would be unsafe to claim a backup, rebuild, migration, restart, or health check without server access.

## Task registration and automation validation

Local automated verification passed for the registered source-processing, analysis, opportunity-generation, approved-opportunity rendering, content-package generation, preview-proxy, cleanup, and scheduler tasks. The worker registration and runtime checks on the VPS remain pending SSH recovery.

The source, opportunity, clip, and content-package handoffs remain idempotent in local tests. Existing multi-brand tests verify project/source visibility isolation and cross-brand destination-account rejection. No public publishing action was run.

## Discord and runtime status

VPS Discord gateway connection, persistent-view registration, API health, readiness, PostgreSQL, Redis, worker ping, scheduler heartbeat, storage, cleanup dry-run, logs, and MxF Labs co-hosting status are pending authentication to the reachable server. Interactive Discord button testing must be performed by a real operator after deployment.

## Required operator-side recovery checks

1. Provide this Codex session with the same authorized SSH method used in the successful interactive `ssh root@198.251.78.178` login, such as an approved temporary deployment key or an authorized agent identity.
2. Do not paste a password or private key in chat.
3. Once authenticated, inspect the local deployment-script diff before any pull, then preserve `.env.ip-bootstrap`, its backup, `build/`, Docker volumes, and database data.

After TCP 22 is reachable, the next safe deployment sequence is: inspect VPS Git status, run the existing backup procedure, `git pull --ff-only`, recreate only API/worker/scheduler/Discord with the active IP-bootstrap Compose profile, then perform the listed runtime and controlled automation checks.
