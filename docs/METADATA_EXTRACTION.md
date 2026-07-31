# Metadata extraction

`app.ingestion.metadata.extract_metadata` parses only the bounded HTML response returned by the safe client. It does not make network calls, execute JavaScript, render a browser, or fetch Open Graph media.

The maintained Beautiful Soup HTML parser is used with bounded, control-character-cleaned values. It extracts HTML title, description, canonical URL, Open Graph title/description/type/image/video fields, Twitter card/title/description/image, author, site name, publication and modified dates, language, final URL, HTTP status, and response type.

Display title selection is deterministic: Open Graph title, then Twitter title, then HTML title. Description selection is Open Graph description, then Twitter description, then ordinary meta description. Raw bounded values and selected values are retained separately in `Source.provider_metadata`.

Relative canonical and media URLs are resolved against the final URL and passed through URL normalization. Canonical values are marked unverified and are never fetched automatically. Open Graph image/video URLs are also unverified metadata only: they are not trusted, downloaded, or otherwise used as authorization to retrieve media.
