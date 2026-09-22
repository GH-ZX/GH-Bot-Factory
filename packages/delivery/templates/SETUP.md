# Your customer-owned installation

This package is an unverified release candidate until installation acceptance is completed.
Its application image must be a published `registry/repository@sha256:…` release built
from the same source as this package. Registry access is required. An image archive is
not embedded: for offline delivery the factory must separately provide `docker save`
output and the published digest, load it with `docker load`, and verify identity locally.

## Files

- `01-database.sql`: complete initial schema for a **new empty dedicated database**.
  Never run it on an existing installation. Later upgrades use the image's Alembic migrations.
- `compose.yaml`: API, worker, Telegram runtime, local Redis and tools, using your database.
- `tenant.ghbf.enc`: encrypted tenant records and credentials. Passphrase supplied separately.
- `manifest.json`: schema identity, image digest reference and SHA-256 for package files.
- `REPORT.html`: customer report in the selected language; open locally or print to PDF.
- `configure.py`, `preflight.py`, `lifecycle.py`, `install.sh`, `update.sh`: destination setup, integrity guards and private operation receipts.
- `backup.sh`: encrypted database/vault backup helper; requirements in UPDATE-RESTORE.md.

## Installation

1. Prepare a dedicated PostgreSQL database or dedicated Supabase project. Keep the database
   password server-only. Use the direct PostgreSQL connection (or session pooler on port 5432),
   not the transaction pooler or Supabase HTTP API. Follow your provider's TLS requirements.
   The app connection uses `postgresql+asyncpg://…`; URL-encode special characters in credentials.
2. In the SQL editor, run **01-database.sql** on the empty database as its owner.
   The script is transactional and refuses duplicate tables. It enables RLS on backend
   tables with no public policies; the app needs a server-only database owner connection.
   Do not expose commerce tables to browser/anonymous clients.
3. Extract the package into a private directory. Restrict access with `chmod 700 .`.
   Compare package checksums with the factory's separately supplied manifest/checksum.
4. Install Docker with Compose support. Run `python3 configure.py`; it writes a private
   destination `.env`, generates new login/signing secrets, and asks for your public HTTPS origin.
   Never reuse or send the factory's environment file. Configure HTTPS/proxy routing to
   `127.0.0.1:8010` on this server; port 8000 is not used.
5. Pause source writes, queues and runtime and confirm the old bot has stopped. Run
   `sh install.sh --source-stopped-empty-destination`. The schema compatibility check runs
   before import; the encrypted bundle passphrase is entered interactively, never as a CLI argument.
   Import is restricted to an empty destination and re-encrypts credentials in its local vault.
6. Customer owner credentials must be set before handoff: existing username/password credentials
   transfer with the encrypted data; the destination's new JWT secret invalidates old sessions.
   The installer does not create a factory bot or give tenant roles platform authority.
   If the owner has no password yet, use the existing authenticated Account password flow
   before export. Do not hand off a tenant until its owner can sign in; this package
   does not create or recover a password automatically.
7. Start: `docker compose up -d redis api worker bot-runtime`.
   Open `/admin/` and launch the MiniApp through the customer's Telegram bot.
   Configure supplier/payment credentials through the admin portal, stored in the local encrypted vault.
8. Complete owner-authorized acceptance before allowing shoppers: catalog, one controlled order,
   funding settlement, support/warranty, restart, and database/vault recovery.

## Things the factory must deliver separately

- Registry access and the verified release image digest (or matching image archive for offline use).
- Encrypted-bundle passphrase over a separate private channel.
- Tenant owner sign-in details; reset the password after handoff.
- Customer bot identity, domain/DNS ownership, and supplier/payment account requirements.
- Acceptance record, exact version and known limitations; never claim unrun checks passed.

Keep `.env`, the encrypted database backup, vault volume (including master key), and
bundle passphrase private. Redis is rebuildable. Do not delete the secret-store volume.


Each configuration generates an isolated Compose project name. Keep it unchanged when
updating so the installation keeps its own vault and Redis volumes. For another store
on the same host, use a separate directory/database and a different available API port.
Never point two customer packages at the same database or vault.

Run `python3 preflight.py` after configuration and before handing the package to an operator.
It checks local configuration and checksums only. Compare the manifest against the factory's
independently supplied checksum; a manifest distributed with a package is not a signature.
Do not modify generated scripts/Compose files: request a regenerated package when needed.
Destination `.env`, update approval and operation receipts are intentionally outside the
immutable package manifest and must stay private.
