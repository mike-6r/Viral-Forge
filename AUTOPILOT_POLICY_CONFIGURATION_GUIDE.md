# Autopilot policy configuration guide

`AutopilotPolicy.config_json` is brand-owned and contains only operational configuration, never credentials. Use these top-level objects: `general`, `discovery`, `source`, `clip`, `render`, `metadata`, `schedule`, `publishing`, and `analytics`.

Important safeguards: source automation requires `require_rights`, `require_moderation`, and `minimum_trust`; clip/render/metadata automation require explicit score thresholds; schedule requires a valid IANA timezone, daily maximum, and minimum spacing; publishing defaults to private and confirmation-required.

Safe partial updates preserve unrelated safeguards. Production rejects unknown levels, invalid timezones, source acceptance without rights/moderation, enabled scheduling without limits/spacing, and automatic Direct Post. Credential references remain on `DestinationAccount` and must be opaque external references.
