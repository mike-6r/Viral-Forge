# Storage architecture

`LocalFilesystemStorage` is the only implemented provider. It uses separate `tmp/` and `assets/` directories below a configurable root outside the source tree by default. Keys are generated UUID-based provider-relative keys and never include filenames, user data, or source URLs.

All storage access goes through the storage interface: create temporary object, stream chunks, read prefix, finalize, open, metadata, delete, and stale temporary cleanup. Resolved paths must stay under the configured root; untrusted keys, separators, absolute paths, symlinks, and escapes are rejected. Finalization uses an atomic local rename and does not overwrite an existing key.

`S3CompatibleStorage` is a reserved interface only; no cloud credentials or network storage are implemented. Uploaded media is not served as a public static directory.
