# Security

Settings are environment-based and production startup rejects a default/short secret, SQLite, and development actors. Development actors require an explicit `X-Development-Actor` UUID and are automatically unavailable in production. Mutations have actor identities. JSON logs redact passwords, tokens, secrets, cookies, and authorization values. No platform credentials are stored or logged by this milestone.

Container build context excludes `.env` and local database files. Schema creation is never triggered by API or worker startup.

Manual URL metadata retrieval uses one asynchronous, TLS-verifying outbound client. It does not trust proxy environment variables, send cookies/authentication headers, follow redirects automatically, fetch media, execute JavaScript, or use a headless browser. DNS and redirect destinations are validated against SSRF rules and HTML response bytes are bounded. Public availability and metadata never establish rights or authorize reuse; Open Graph media URLs are unverified and not fetched.

Manual media uploads stream into opaque temporary storage, inspect container signatures, enforce actual byte limits, clean partial files, and never use a client filename as a path. Uploaded media is not public, is not malware-scanned, and does not become reusable or approved merely because storage succeeds.
