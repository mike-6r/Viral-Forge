# Domain model

Core records: users/roles, sources/source policies, content items/content sources/media assets, rights and moderation assessments, ranking assessments, processing and publishing jobs, clip candidates, review decisions, published posts, performance snapshots, and audit events. All foundational records use UUID identities and timezone-aware timestamps. Provider-specific evidence/metadata is JSONB on PostgreSQL.

Media assets retain provider-relative storage keys, detected and declared type, container, bytes, SHA-256, uploader/source provenance, correlation, and a verification-required asset state. A shared physical SHA-256 asset does not merge separate source or rights provenance.
