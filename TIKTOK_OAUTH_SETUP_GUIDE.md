# TikTok OAuth setup

The operator creates a brand-owned TikTok destination account with an opaque credential reference, then starts OAuth from the brand-scoped TikTok endpoint. ViralForge creates a random state, persists only an HMAC digest and expiry, and redirects to TikTok Login Kit.

The callback validates the one-time state before exchanging the code server-side. It never persists or returns access/refresh token values. The external credential manager must receive the exchanged token set through the organization-approved vault integration and expose it through the destination's configured credential reference. Then run the connection/capability verification.

If this external write integration is not available, OAuth is intentionally left at `CREDENTIAL_REFERENCE_REQUIRED`; do not bypass the boundary by adding token fields to ViralForge.
