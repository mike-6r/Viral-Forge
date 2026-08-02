# Rendered Media Quality operator guide

From a finished clip in Discord, select **Media Quality**. The first selection queues local inspection; select it again after the worker completes to see the report. The report is advice only.

- **View Issues** shows concise issue evidence and recommended review actions without filesystem paths, frame images, tokens, or raw JSON.
- **Regenerate** creates a new inspection version; it does not rerender the clip.
- **Add Note**, **Approve Advice**, and **Reject Advice** record an operator opinion using optimistic locking. They do not approve/reject the clip itself.
- Use the existing **Refresh Preview Link** control to inspect the actual result visually.

Treat low-confidence subtitle, safe-area, and crop findings as a cue to inspect the private preview. Do not use an advisory approval as rights, moderation, content-package, or publish approval.
