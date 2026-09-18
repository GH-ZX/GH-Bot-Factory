# ADR-047: Encrypted Single-Tenant Portability and Offline Destination Restore

- **Status:** Accepted
- **Date:** 2026-09-19
- **Refines:** ADR-044 (Dedicated Deployment and Source License Handoff)

## Context

Phase 14.5 established single-tenant deployment handoffs, but the initial export implementation generated unencrypted JSON snapshots containing plaintext secrets from `SecretStorage`, had no schema fingerprint binding, lacked an automated offline destination restore path, and prematurely claimed `HANDED_OFF` status upon desired-state bot deactivation alone.

Key architectural, security, and portability constraints:
1. **Secret Safety (Law 3):** Secret values must never be stored, exported, or transferred in plaintext. Export bundles leaving the multi-tenant cluster must be encrypted at rest using an operator-provided passphrase.
2. **Portable State Law (Law 12):** Laptop/VPS portability consists of PostgreSQL plus the encrypted secret vault; Redis is rebuildable and `.env` is destination-owned. Restoring a tenant must not overwrite existing installations or adopt source-owned environment URLs.
3. **Multi-Tenancy & Data Isolation (Law 2):** A single-tenant export must contain exclusively the target tenant's records. Cross-tenant rows or platform control plane state (platform audit logs, SaaS plans, global leads, quotes) must never leak into the exported artifact.
4. **Telegram Polling Conflict Invariant (Law 8):** Two runtimes must never poll the same Telegram bot token concurrently. Desired-state bot disable on the source must not be confused with verified runtime stop.

## Decision

1. **Encrypted Snapshot Envelope (`ghbf-tenant-encrypted-v2`):**
   - Key derivation uses Scrypt (salt=16 bytes, N=32768, r=8, p=1, key_length=32) combined with a Fernet symmetric cipher.
   - Requires an operator-supplied export passphrase of 16 to 1024 characters, entered interactively via CLI (`platformctl`) or Admin dialog (`#handoffExportDialog`).
   - Plaintext legacy exports are explicitly rejected on import.

2. **Strict Single-Tenant Isolation & Table Registry:**
   - Tables are partitioned explicitly into `INCLUDED` (tenant-scoped) and `EXCLUDED` (platform control plane, SaaS plans, public inquiries, quotes, platform audit logs).
   - Any addition or removal of database tables in the application model requires an explicit portability review.
   - Export validates that every row in `INCLUDED` tables belongs to the target `tenant_id`. Foreign keys cannot reference records outside the exported tenant boundary.

3. **Secret Encapsulation & User Sanitization:**
   - Secrets required by tenant records (`token_secret_ref`, `secret_ref`, `credentials_ref`, `webhook_secret_ref`) are resolved from the source `SecretStorage` and encrypted inside the snapshot envelope.
   - Exported artifacts on disk (`tenant.ghbf.enc`) are written with private file permissions (`0o600`).
   - Global user authentication data (`hashed_password`, `email`, `first_name`, `last_name`) is stripped, preserving user identity (`telegram_id`) and incrementing `token_version` to invalidate existing sessions.

4. **Source Quiescence Requirement:**
   - Export requires explicit confirmation that source writes and workers are paused (`confirm_quiesced: bool = True` / `--confirm-quiesced`).
   - On PostgreSQL, the export snapshot executes under `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY` to guarantee consistency across tables.

5. **Safe Offline Destination Restore (`scripts/import_tenant_bundle.py`):**
   - Restore target must be an empty, freshly migrated database schema (`count == 0` on all tables). Existing installations are never overwritten.
   - Encrypted secrets are decrypted from the bundle and inserted into the destination's `SecretStorage` under uniquely namespaced references (`GHBF_IMPORT_<namespace>_<id>_<column>`). If restore fails, created secret references are cleaned up.
   - Initializes `system_install_state` (`is_initialized = True`).
   - Resets `admin_public_url` and `miniapp_public_url`, preserving destination environment ownership.
   - Bots remain disabled by default unless `--activate` is supplied alongside `--confirm-source-stopped`.

6. **Separation of Desired-State Disable from Cutover Completion:**
   - Managed runtime deactivation sets desired-state `is_enabled = False` and increments `runtime_revision`, but status remains `EXPORTED` (or `READY_FOR_EXPORT`).
   - The platform never marks `HANDED_OFF` until observed runtime shutdown and customer cutover validation.
   - Admin UI accurately states "Polling disable requested — verify runtime has stopped".

7. **Operator Bundle Download:**
   - Platform API endpoint `GET /api/v1/platform/sales/handoffs/{handoff_id}/bundle` allows authenticated platform operators to download the encrypted artifact with SHA-256 integrity verification.
