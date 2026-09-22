# Customer-approved updates and restoration

The Bot care & updates screen records owner-reported actions. It does not execute SSH,
install software, verify evidence, or permit the factory to log in as a tenant.

1. Open a maintenance issue. Document reproduction, diagnosis, whether the fix is in
   the shared core or customer-specific, and the proposed fixed release version.
   For shared fixes, copy only a sanitized reproduction into the factory's development backlog;
   never copy another tenant's profiles, orders, credentials or private logs.
2. Plan an update using the current and proposed **immutable image digests**. Document
   release notes, database changes, downtime, and rollback requirements. The customer owner
   reviews these and explicitly records approval.
3. Stop customer traffic and all API, worker and bot runtime processes. Back up the
   PostgreSQL database (Supabase backup/export or encrypted `pg_dump`) **and the entire
   encrypted vault volume including its master key**. Keep the old `.env` and Compose file
   privately. Record backup location and checksum, not passwords, in the update record.
   Backups must correspond to the same stopped installation state.
4. Record BACKED_UP. Set GHBF_IMAGE in destination `.env` to the approved new digest.
   Run `sh update.sh --approved-and-backed-up`. This stops only this Compose project,
   pulls images and applies Alembic upgrades. On failure services stay stopped;
   do not repeatedly retry unknown migrations without diagnosis.
5. When authorized, start services and complete acceptance. Record INSTALLED with the
   actual image identity, schema revision and acceptance evidence. Mark the issue resolved
   only after the customer confirms the fix. An image being downloaded is not proof of a fix.

## Rollback

Never assume an older image works with a newer database. Read the release's migration notes.
Stop all application services first. Restore the pre-update PostgreSQL database and encrypted
vault together into an isolated destination, set the old image digest, restore the matching
private environment, and perform owner-authorized recovery acceptance before cutover.
Do not merge restored and live ledgers. New business activity after the backup requires a
reconciliation plan before restoration; a simple restore would discard that activity.

Record ROLLED_BACK with image, backup reference, cause, restored schema and actual acceptance
results. Retain failed-deployment evidence without secrets so the shared-core fix can improve.

## Optional support access

The customer owner can grant read-only diagnostics for 1–72 hours from Bot care & updates.
The one-time code is sent privately. The factory uses the customer's HTTPS origin and
`X-Support-Token` request header at `/api/v1/maintenance-access/diagnostics`.
Never put a token in a URL, shell history, issue note or screenshot. Every successful read
is logged. Revocation applies to subsequent reads; already-delivered diagnostics cannot be
recalled. Grants do not transfer in exported tenant bundles. No public ingress is needed
for normal Telegram polling, and support is never mandatory: the customer can instead
share a sanitized issue report and apply the approved image locally.

Actual repairs ship as reviewed, versioned images. This diagnostic interface cannot run
shell commands, edit a database, install a patch, or bypass customer approval.


## Guarded update record

Before `update.sh`, copy `update-approval.example.json` to `update-approval.json`.
Record the exact previous/approved digests, actual customer approval, stopped writes,
database AND vault backup checksums, a private backup reference and rollback notes.
The script refuses missing or mismatched evidence. These are operator declarations;
it does not remotely prove approval or verify that a backup is restorable.

`install.sh` and `update.sh` keep `.installation.json` and `.operation-lock` privately.
A failed operation deliberately keeps its lock and applications stopped. Do not remove
it and retry blindly. Diagnose the failed step; preserve these files and the database state.
For a failed first import, prepare a fresh empty dedicated destination and new package.
For a failed migration, restore the matched pre-update database/vault/environment/image
into an isolated destination, then complete authorized recovery acceptance before cutover.
A missing local receipt on an existing installation requires operator recovery review;
the script does not guess an installed version or permit a fresh import over existing data.

## Encrypted backup helper

Install host `pg_dump` compatible with the database, Python 3.11+ and GPG. Configure a
private PostgreSQL service in `.pg_service.conf` and a mode-600 `.pgpass` for the **same
destination database** as DATABASE_URL. Include the provider's required TLS settings.
Do not put passwords in command arguments. Import/verify the backup recipient public key.

Run `PGSERVICE=customer-db bash backup.sh --approved-stop-writes RECIPIENT_FINGERPRINT`.
The helper stops this package's application containers, exports the database and full vault,
encrypts both, and writes a checksum receipt. It leaves applications stopped on success
or failure. Check the intended database identity before running; the host service is an
operator-owned connection, not automatically derived from the application's URL.

Copy encrypted artifacts off the server and keep the decryption private key separately.
The receipt supports the installation-evidence form and update approval record; it is not
a successful restore drill. Restart only after deciding whether to continue the approved
update or resume the unchanged installation. Never restart after a failed migration
without following the recovery workflow above.
