# TikTok reconciliation

Draft upload transfer success enters `OPERATOR_COMPLETION_REQUIRED`, not published. The operator opens TikTok's inbox notification, edits/posts or rejects/abandons the draft, then records `POSTED`, `REJECTED`, or `ABANDONED`. A final TikTok URL may be added only after operator confirmation.

For Direct Post, ViralForge performs bounded official status refreshes. A connection failure after initialization or transfer becomes `UNKNOWN_REMOTE_OUTCOME`; do not retry blindly. Inspect TikTok and record the result through manual reconciliation. TikTok metrics can be entered manually in the existing analytics endpoint; unavailable metric values remain null.
