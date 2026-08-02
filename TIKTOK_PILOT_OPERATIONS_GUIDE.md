# BodycamsDailyHQ TikTok pilot operations

Configure BodycamsDailyHQ through its brand settings and destination account, never by hardcoded ID.

- Keep emergency pause enabled until a test account is connected.
- Use `DRAFT_UPLOAD` as the default. It transfers to the TikTok inbox but does not publish.
- A content-ready clip still requires an explicit TikTok request and a separate Confirm Transfer action.
- Maximum pilot default: three transfers per day, one brand-owned destination, and no public Direct Post before TikTok audit.
- Keep source attribution, source acceptance, rights approval where required, moderation approval, rendered-clip approval, and content-package approval intact.
- Do not remove agency overlays, timestamps, Axon markings, or source branding.

For Direct Post, ViralForge queries creator capabilities immediately before transfer. In DEVELOPMENT or UNAUDITED state, privacy is forced to `SELF_ONLY`.
