# Private previews behind Caddy

Set `VIRALFORGE_PREVIEW_PUBLIC_BASE_URL` to an HTTPS hostname before enabling previews in production. Caddy terminates TLS and proxies only the application; never mount or serve the local media directory.

```caddyfile
preview.example.invalid {
    reverse_proxy api:8000 {
        flush_interval -1
        transport http { read_timeout 20m write_timeout 20m }
    }
    header {
        -Server
    }
}
```

Use DNS for the hostname, allow public TCP 80/443 only, and retain the container port on the private Docker network. Apply request-rate limits at Caddy/the perimeter, especially to `/preview/*` and `/api/v1/previews/*/media`. Caddy forwards byte-range requests by default. Size bandwidth for concurrent reviewers and configure persistent storage separately from the reverse proxy.

If a preview URL leaks, revoke the clip's grants from the operator API; the raw token is not stored and cannot be recovered. Use the cleanup Celery task on a bounded schedule (for example hourly).
