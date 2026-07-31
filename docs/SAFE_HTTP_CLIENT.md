# Safe outbound HTTP client

`app.ingestion.http.SafeOutboundHttpClient` is the sole outbound HTTP boundary for ingestion metadata retrieval. Route handlers and parsers do not create HTTP clients.

It uses asynchronous `httpx` with TLS verification enabled, proxy environment variables disabled, no cookies or authentication headers, a controlled user agent, separate connect/read/write/pool timeouts, and an overall deadline. Connections and streamed responses are deterministically closed.

Only HTTP(S) requests are accepted. DNS is resolved immediately before every initial request and redirect. Every answer must be globally routable; the client rejects local/private/loopback/link-local/multicast/unspecified/reserved/CGNAT addresses, local host aliases, and common cloud metadata hosts. A hostname with both a public and a private answer is rejected.

Redirects are handled manually. Each `Location` is resolved relative to the current URL, normalized, DNS-validated, policy-checked by the caller, and limited. The client never follows an unchecked redirect.

The selected `httpx` stack resolves DNS again when it establishes a socket. Therefore preflight validation reduces DNS-rebinding exposure but cannot pin the connection to a prevalidated IP address; this is an honest time-of-check/time-of-use limitation. Decisions are not cached indefinitely and redirects repeat validation.

Responses are streamed and limited by both `Content-Length` (when supplied) and decompressed bytes actually read. The default limit is 1,000,000 bytes. Only `text/html` and `application/xhtml+xml` are consumed; video, media, binary, and ambiguous types are rejected before body iteration.
