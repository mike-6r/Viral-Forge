# ViralForge remaining limitations

Date: 2026-08-02

## Discord ephemeral-component lifetime (platform limitation)

Discord cannot revive a component attached to an expired ephemeral message or to a message issued before a bot restart. New guided project cards now provide a `/viralforge home` recovery instruction, and the command reloads persisted project state. This is not a data-loss or workflow blocker.

## Conservative warning scope

The local content-package provider now raises a review warning only when persisted source metadata contains one of the deliberately narrow high-signal terms: `shooting`, `stabbing`, `homicide`, or `gunfire`. It does not infer sensitive content from ambiguous transcript language or invent an event classification. Human source, rights, moderation, and package review remain required.

## Alembic cyclic foreign-key autogeneration warning

The PostgreSQL Alembic check completed with no new upgrade operations, but SQLAlchemy warns that it cannot fully order the existing cyclic foreign-key group spanning correction plans, media assets, production clips, and rendered-media inspections. Current explicit migrations and the disposable schema comparison remain valid. Resolving the warning would require a separately reviewed schema change to alter those foreign-key declarations; it is not safe to change that deployed schema topology during this no-migration hardening pass.

## Preserved VPS-local items

The deploy process continues to preserve the existing executable-bit change on `scripts/production/deploy-ip-bootstrap.sh`, protected environment files, the VPS build directory, and unrelated VPS-local untracked files. They are not committed or removed by this hardening pass.
