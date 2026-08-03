# Secure mobile downloads

Full-quality downloads are separate from preview proxies. A grant is issued only for the authoritative `RENDERED_CLIP` asset linked to the approved project/clip; paths and storage keys never come from the browser.

- A random token is stored only as a one-way digest in `clip_download_grants`.
- The browser link holds the raw token in its URL fragment. Fragments are not sent in the HTTP request or ordinary proxy access logs. The page sends it only in the `Authorization: Bearer` header to the download API.
- Grants default to a 15-minute TTL and two access attempts. They can be revoked per clip, expire automatically, and are audited.
- The streaming endpoint supports range requests, uses attachment disposition, sets `Cache-Control: private, no-store`, and does not proxy/load the whole MP4 into application memory.
- Production grants require the configured HTTPS public URL. Do not issue them from temporary HTTP/IP-bootstrap deployment.
- Active unexpired download grants hold the related media from retention cleanup. Revocation or expiry releases that temporary protection.

Configure only non-secret limits in the protected environment:

```dotenv
VIRALFORGE_DOWNLOAD_TOKEN_TTL_SECONDS=900
VIRALFORGE_DOWNLOAD_MAXIMUM_ACCESS_COUNT=2
```

Use a high-entropy `VIRALFORGE_PREVIEW_HASHING_SECRET` in production. Never paste a generated download link into a public channel.
