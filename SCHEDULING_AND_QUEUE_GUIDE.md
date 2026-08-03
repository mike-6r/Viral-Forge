# Scheduling and queue guide

ViralForge ranks existing `PostingQueueItem` records per brand and persists a short explanation and evidence. It does not duplicate the posting queue. Operators can prioritize, hold, or release through the existing queue workflow; automation records the result for future audit.

A schedule reservation binds exactly one brand, destination, queue item, selected clip, content-package generation version, policy version, provider mode, privacy, timestamp, and confirmation requirement. The database prevents a queue item from being reserved twice and prevents double-booking the same destination/time.

Reserved slots do not submit an upload. A missed, blocked, or uncertain provider action becomes an exception for review and reconciliation.
