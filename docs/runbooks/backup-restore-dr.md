# Backup / Restore / Disaster Recovery

## Policy
PostgreSQL is the authoritative durable store. Redis is ephemeral coordination/cache state and is not restored as business truth. Production backups must be encrypted unless an operator explicitly accepts unencrypted backup risk.

## Backup
Run `./scripts/backup_postgres.sh`. Configure `BACKUP_GPG_RECIPIENT` in production. Keep backup encryption keys outside the application host and repository. Default retention is 14 days (`BACKUP_RETENTION_DAYS`). Copy encrypted backups off-host after creation.

## Restore drill
At least monthly, run `./scripts/verify_backup_restore.sh <backup>` against the newest backup. It restores into an isolated temporary database, verifies Alembic metadata and basic tenant readability, then destroys the temporary database.

## Destructive restore
Stop API/worker/bot-runtime, snapshot the current database, set `RESTORE_ACKNOWLEDGE_DATA_LOSS=I_UNDERSTAND`, then run `./scripts/restore_postgres.sh --confirm <backup>`. Run migrations, `make verify-postgres`, and smoke checks before reopening traffic.

## Recovery objectives
Target RPO is the backup interval. Target RTO is the time to provision PostgreSQL, decrypt a backup, restore, migrate, verify, and resume. Measure these during drills; do not claim an RPO/RTO that has not been demonstrated.
