# Feed security

Feed retrieval uses the centralized outbound client: HTTPS/HTTP normalization, fresh DNS checks, SSRF blocking, bounded redirects, TLS verification, disabled environment proxies/cookies/authentication, total and per-operation timeouts, and a bounded streamed response. Only XML media types needed for RSS/Atom are accepted for feeds.

`defusedxml` parses the bounded response and rejects DTD/entity attacks. Root-format failures fail the run without exposing parser details; an invalid entry URL is recorded as a bounded entry-level failure while other entries continue. Raw XML, unbounded provider metadata, enclosure bodies, remote error bodies, and stack traces are neither persisted in the API response nor logged by the feed service.
