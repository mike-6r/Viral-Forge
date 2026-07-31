# URL normalization

Manual URLs are normalized by `app.ingestion.url.normalize_url`: only HTTP(S) is accepted; credentials, local hosts, and non-global IP literals are rejected; hosts are IDNA-normalized and lower-cased; default ports, fragments, repeated path separators, and known tracking query parameters are removed.

Normalization is necessary but not sufficient network protection. The safe HTTP client separately resolves and validates DNS for every request and redirect. Canonical and metadata URLs are normalized but remain unverified and are never automatically fetched.
