# Guided Operator Experience

## Outcome

The Discord control center now routes blocked operator workflows to an actionable next step instead of leaving a static error message.

## Guided flows delivered

- **No discovery source:** `Continue Working` and `Find Videos` open **Discovery Setup** for the active brand.
- **Source setup:** the operator can select a YouTube Channel, Manual Import, or see clear **Coming soon** alternatives for playlist, RSS, website, and templates. Every setup page includes Help and Back.
- **YouTube validation:** public channel URLs, handles, and IDs are verified through the official YouTube Data API only. The confirmation shows channel name, thumbnail, video count, latest upload, and source status before enabling it.
- **Discovery handoff:** enabling a channel records a brand-scoped source and audit event, then offers **Run Discovery Now**. The bounded scan reports new and duplicate items before offering review.
- **Manual video intake:** adding a URL requires a source and rights confirmation before a project is created. Invalid input has a retry/manual path.
- **Publishing readiness:** the empty publishing state offers a secure YouTube account setup explanation and a route back to video discovery. Discord never collects OAuth tokens, passwords, cookies, or raw credentials.
- **Missing access:** every existing authorization response now presents **View Required Roles**, **Contact Administrator**, and **Back**. The helper only explains the configured roles; it never bypasses authorization or grants a role.

## Safety preserved

- Existing brand scoping, source acceptance, rights review, moderation, approval, audit, idempotency, and publishing confirmation controls are unchanged.
- Channel validation rejects non-YouTube hostnames and never scrapes pages or uses browser automation.
- A source is not created until an operator explicitly selects **Enable Source**.
- Scanning is explicitly initiated and uses the existing rate-limited discovery service.

## Verification

Completed locally on 2026-08-01:

```text
python -m pytest -q       PASS
python -m ruff check .    PASS
python -m mypy app        PASS
```

The test suite includes the discovery setup choice list, guided empty-state action views, YouTube reference validation, and a mocked official-API channel lookup that confirms the expected metadata is normalized without a live credential.

## Deployment and visual verification

The implementation is ready to deploy. Manual Discord screenshots require a reachable VPS and a signed-in Discord operator session; neither is available from this local verification environment. Deployment should be followed by a bot restart and a `/home` check in Discord, then the no-source discovery wizard can be exercised with the configured YouTube API key.
