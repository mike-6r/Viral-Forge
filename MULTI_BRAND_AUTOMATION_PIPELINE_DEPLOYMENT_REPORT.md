# Multi-Brand Automation Pipeline Deployment Report

Date: 2026-08-01

## SSH result

Deployment is blocked before authentication. The configured VPS address is `198.51.178.178`.

```text
TCP port 22 reachable: False
ssh: connect to host 198.51.178.178 port 22: Connection timed out
SSH exit code: 255
```

Verbose SSH reached only the TCP connection attempt; it did not reach host-key exchange or authentication. No private-key material was read or printed.

## Git state

- Expected application commit: `5f88343` (`Automate approved opportunity rendering`)
- Local repository: pushed to `origin/main`
- Previous VPS commit: not inspectable because SSH is unavailable.
- Deployed VPS commit: not inspectable; no pull was attempted.
- VPS working tree and remote: not inspectable; no files or environment settings were modified remotely.

## Backup and service rebuild

Not attempted. The required production backup must run on the VPS before a pull, and it would be unsafe to claim a backup, rebuild, migration, restart, or health check without server access.

## Task registration and automation validation

Local automated verification passed for the registered source-processing, analysis, opportunity-generation, approved-opportunity rendering, content-package generation, preview-proxy, cleanup, and scheduler tasks. The worker registration and runtime checks on the VPS remain pending SSH recovery.

The source, opportunity, clip, and content-package handoffs remain idempotent in local tests. Existing multi-brand tests verify project/source visibility isolation and cross-brand destination-account rejection. No public publishing action was run.

## Discord and runtime status

VPS Discord gateway connection, persistent-view registration, API health, readiness, PostgreSQL, Redis, worker ping, scheduler heartbeat, storage, cleanup dry-run, logs, and MxF Labs co-hosting status are all pending because the server is unreachable over SSH. Interactive Discord button testing must be performed by a real operator after deployment.

## Required operator-side recovery checks

1. Confirm the provider firewall/security group allows inbound TCP **22** from your current public IP.
2. Confirm the firewall policy is attached to the VPS with IP `198.51.178.178`.
3. Use the provider console/rescue console to run `sudo ufw allow OpenSSH`, `sudo systemctl enable --now ssh`, and `sudo systemctl status ssh`.
4. Confirm the assigned public IPv4 address has not changed and that `root` is the intended SSH username.
5. Confirm the correct private key is loaded locally. Do not paste it into chat.
6. If the console cannot restore SSH, use the provider's rescue/recovery process before retrying deployment.

After TCP 22 is reachable, the next safe deployment sequence is: inspect VPS Git status, run the existing backup procedure, `git pull --ff-only`, recreate only API/worker/scheduler/Discord with the active IP-bootstrap Compose profile, then perform the listed runtime and controlled automation checks.
