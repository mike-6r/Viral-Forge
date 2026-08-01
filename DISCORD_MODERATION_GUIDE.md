# Discord Moderation Guide

Automod is deterministic and configuration-driven in `config/discord/automod.yml`. It checks obvious credential patterns, Discord invites, excessive mentions, and repeated messages. Detected values are deleted where bot permissions allow; only redacted category, length, and case metadata are stored.

Staff review cases with `/admin moderation-cases`. Members may use `/account appeal`. Do not test with real secrets. Use a clearly fake token-like string in a controlled test channel and rotate any real value that was ever sent.
