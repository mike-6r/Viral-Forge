# Manual-publish pilot

This is the BodycamsDailyHQ operator workflow. It is deliberately Discord-first and **does not publish through any platform API**.

1. In Discord, select **Find Videos** and enable a public, permitted source, or add one permitted video manually.
2. Review each source and clip suggestion. Approve no more than the clips that are appropriate for the project (the production pipeline caps the set at three).
3. Review the rendered clip and use **Download Full Quality**. Open the short-lived link on the phone, save the MP4, and upload it manually in the platform app.
4. Return to the approved content package, select **Record Manual Post**, and provide the platform, destination label, public post URL, and optional notes.
5. Enter available platform metrics at the 1h, 6h, 24h, 72h, and 7d checkpoints. A checkpoint can be completed, skipped, or snoozed; omitted metrics remain unavailable rather than being estimated.

The system records an audit event for each grant, manual-publication record, analytics entry, and checkpoint decision. It never asks for a social-platform password, cookie, OAuth token, or credential in Discord.

## Pilot boundaries

- The operator is responsible for source authorization, rights, safety review, and the final manual upload.
- Manual post recording accepts only a public HTTPS URL appropriate for the selected platform; a repeated URL is rejected idempotently.
- Recording a post does not create a publishing request, queue an upload, schedule content, or call a provider.
- Reports only use persisted manual/official metrics and identify unavailable values as unavailable.
