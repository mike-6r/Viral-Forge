# Manual Publish Pilot and Mobile Download Workflow

## Delivered locally

- Migration `0033_manual_publish_mobile_download` adds durable download grants, manual publications, five manual analytics checkpoints, and manual snapshot linkage.
- Discord clip review now offers a short-lived full-quality download action. Approved content packages offer a manual post record modal.
- The API supports issuing/revoking a clip grant, a mobile browser page, range-streamed MP4 download, manual public-post recording, manual metric entry, checkpoint listing, and acknowledgement/snooze/skip actions.
- Manual records are brand-scoped and idempotent by platform/post URL. They require the exact approved content package and authoritative rendered asset.
- Analytics dashboard aggregation accepts both existing provider-publish snapshots and manual post snapshots. No metric is invented.

## Security and operational boundaries

- No TikTok, Instagram, Facebook, YouTube, or other provider upload is invoked by this workflow.
- No credential, password, cookie, OAuth token, or raw API key is stored in the added tables or requested by Discord.
- The browser capability token is hashed at rest and placed after `#` in the link so it is not transmitted to ordinary HTTP/proxy logs.
- Production full-quality download grants require HTTPS. Temporary IP bootstrap should be used only for non-download health checks.

## Verification completed

- Focused tests: `6 passed` for preview, manual download, manual post, checkpoints, and analytics integration.
- Ruff: passed for the changed application and test modules.
- Mypy: passed for the changed application modules.
- Disposable SQLite clean base-to-head upgrade, downgrade of `0033`, and re-upgrade: passed.

## Not yet verified

The system has not been deployed during this local implementation and no live BodycamsDailyHQ source, real phone download, or real public post URL has been tested. Those require an operator on the VPS with the existing protected environment, permitted source, and HTTPS hostname.
