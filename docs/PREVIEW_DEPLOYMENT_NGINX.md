# Private previews behind Nginx

Production previews require HTTPS and a public base URL. Do not use `alias`, `root`, or a static location for ViralForge storage.

```nginx
server {
  listen 443 ssl http2;
  server_name preview.example.invalid;
  # TLS certificates intentionally omitted.
  location / {
    proxy_pass http://api:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_buffering off;
    proxy_read_timeout 1200s;
    proxy_send_timeout 1200s;
  }
  # Add limit_req here or at the edge. Preserve Range and If-Range headers.
}
```

Expose only 80/443 through the firewall and keep API, PostgreSQL, Redis, and storage volumes private. Nginx proxies application byte responses and must not map any media directory. Use a valid DNS name, HTTPS certificates, and timeouts suitable for video streaming.

For an incident involving a leaked link, revoke its preview grants, inspect audit records, and rotate the preview hashing secret if systemic compromise is suspected. Links are private capabilities; do not put them in public channels.
