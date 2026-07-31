# Network safety

ViralForge rejects SSRF destinations through URL checks plus fresh DNS validation. It blocks localhost aliases, private and reserved IPv4/IPv6 ranges, link-local addresses including `169.254.169.254`, multicast, unspecified and CGNAT ranges, cloud metadata aliases, and DNS answers containing any disallowed address.

Only HTML/XHTML metadata documents up to the configured bounded decompressed byte limit are streamed. Automatic redirects, proxy inheritance, cookies, authentication headers, browser execution, and Open Graph media fetching are not used.

DNS resolution cannot be connection-pinned with the current high-level HTTP transport, so a DNS rebinding TOCTOU window remains between validation and connection. The client avoids persistent trust decisions and revalidates every redirect.
