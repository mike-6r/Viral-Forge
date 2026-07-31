# Upload security

Uploads are streamed and checked against actual received bytes; `Content-Length` is not trusted. Empty, oversized, malformed, unsupported, and MIME-mismatched uploads fail with stable safe categories and temporary files are removed.

Original filenames are bounded display metadata only. Paths, drive letters, UNC forms, separators, controls, nulls, Unicode separator lookalikes, and common reserved Windows names are rejected. Local absolute paths, file contents, multipart bodies, cookies, authorization values, and storage roots are never returned by the API or recorded in audit payloads.

Container detection is intentionally lightweight and identifies only supported container signatures. It is not a malware scan and does not validate codec, frame, or playable-media integrity.
