# Backup and restore

Run `scripts/production/backup.sh` daily with `BACKUP_DIR` on a persistent encrypted volume. It creates a compressed PostgreSQL custom-format dump, verifies it is readable, and prunes only timestamped backups older than `BACKUP_RETENTION_DAYS`.

Run `scripts/production/restore-verify.sh backups/viralforge-...dump` at least monthly. It restores only into the explicitly named disposable database `viralforge_restore_verify`, checks it, and drops that disposable database. It never overwrites the active `viralforge` database.

Before a production update, make a backup, record `alembic current`/`heads`, then run the deploy script. Do not automatically downgrade production after a migration failure. Stop affected services, restore the verified backup into a controlled maintenance window, and investigate before retrying.
