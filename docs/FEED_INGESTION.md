# Feed ingestion

Milestone 2C provides synchronous, manual RSS 2.0 and Atom 1.0 ingestion for an existing active source. The feed API is under `/api/v1/feeds`: registration, list/detail, versioned operational PATCH, revalidation, manual run, pause/activate/block, entry history, and run history. Registration and revalidation fetch and validate only the feed document; only a manual run creates content.

Runs are bounded by the effective recent-item window and maximum item limit. Values resolve as a permitted run override, feed value, source policy value, then application default, and are clamped by configured absolute limits. Dated items older than the window are not imported; extreme future timestamps are rejected as malformed. Undated entries remain bounded and use provider order only as a final deterministic fallback.

There is deliberately no scheduler, historical backfill, enclosure download, entry-page retrieval, media download, browser execution, or social-platform scraping in this milestone.
