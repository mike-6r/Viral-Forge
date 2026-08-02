# TikTok developer app setup

ViralForge uses only TikTok Login Kit and the official Content Posting API v2.

1. Create a TikTok developer application and enable Login Kit and Content Posting API.
2. Register the exact callback `https://viralforge.mxf-labs.com/api/v1/oauth/tiktok/callback` after the production hostname is live.
3. Request `user.info.basic` and `video.upload`; request `video.publish` only if Direct Post is required and TikTok approves it.
4. Put the client secret in the approved external secret store. Configure only its `env://...` reference in ViralForge. Never paste the secret, OAuth code, access token, refresh token, or cookie into Discord, the API, database, or repository.
5. Start with `DEVELOPMENT` or `UNAUDITED`, `DRAFT_UPLOAD`, and public Direct Post disabled. TikTok restricts unaudited Direct Post to private viewing.

The IP-bootstrap profile intentionally rejects TikTok OAuth. A trusted HTTPS hostname is a prerequisite, not an optional improvement.
