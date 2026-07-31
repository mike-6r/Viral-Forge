# VPS security checklist

- [ ] Ubuntu LTS, automatic security updates, correct timezone, and NTP enabled.
- [ ] Non-root sudo account; SSH key-only, root login and password authentication disabled.
- [ ] UFW permits SSH, HTTP, HTTPS only; 5432 and 6379 have no public rule.
- [ ] Docker installed from its official repository. Docker-group membership grants root-equivalent access.
- [ ] `.env.production` is root/owner readable only (`0600`); it is never committed or copied into reports.
- [ ] Use long unique database/API/preview secrets; rotate after exposure. Keep OAuth credentials outside database fields as opaque `env://` references.
- [ ] Confirm Caddy is the only public service and no storage directory is configured as a static root/alias.
- [ ] Configure encrypted off-server database backups and periodically run restore verification.
- [ ] Optionally install fail2ban and monitor SSH, Caddy, API, worker, scheduler, and disk alerts.

These are VPS-owner actions. This milestone does not change the local Windows host.
