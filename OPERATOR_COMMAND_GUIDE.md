# ViralForge operator commands

## Rendered media quality

On a finished clip, use **Media Quality** in Discord. It queues bounded local FFprobe/FFmpeg evidence collection and remains advisory. Use **View Issues** and the private preview, then record an advice decision if useful. It does not change the clip approval or post anything.

## TikTok publishing pilot

1. Finish the HTTPS and TikTok developer prerequisites in [TIKTOK_DEVELOPER_APP_SETUP_GUIDE.md](TIKTOK_DEVELOPER_APP_SETUP_GUIDE.md).
2. Create a `TIKTOK` destination account under the intended brand with an opaque external credential reference.
3. In Discord use Content Ready → Set Up TikTok. On IP-bootstrap it will explain the HTTPS requirement; it never accepts a token in Discord.
4. Create either an Upload as TikTok Draft or a Test Private Direct Post request, inspect the provider mode and capability limits, then press Confirm Transfer exactly once.
5. A draft reaches `OPERATOR_COMPLETION_REQUIRED`; complete posting in TikTok's inbox and record the final outcome. Do not call it published before that operator completion.
6. For `UNKNOWN_REMOTE_OUTCOME`, stop and reconcile using the official TikTok status/result; never retry an uncertain upload blindly.

Use the emergency pause before an incident or account concern: set `VIRALFORGE_TIKTOK_EMERGENCY_PAUSE=true`, then recreate API, worker, scheduler, and Discord. This prevents new TikTok transfers without deleting audit history.
