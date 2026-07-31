# Manual media uploads

`POST /api/v1/ingestion/upload` accepts authenticated multipart uploads from owners, administrators, and editors. It streams fixed-size chunks to an opaque temporary object, hashes bytes incrementally, validates a supported container signature, applies source policy, finalizes storage atomically, and creates provenance, an ingestion job, a media asset, lifecycle transitions, and audit events.

Supported signatures are MP4, MOV, WebM, and Matroska. Filename extensions and multipart MIME declarations are not trusted; a mismatched declaration is rejected. No FFmpeg, video processing, clipping, frame inspection, or malware scanner is used in this milestone.

Exact SHA-256 duplicate detection reuses the existing physical asset but records the new source provenance and duplicate relationship. It does not perform perceptual duplicate detection. Successful upload does not prove ownership; an uploader rights declaration is only an unapproved claim and rights/moderation approval remains mandatory.
