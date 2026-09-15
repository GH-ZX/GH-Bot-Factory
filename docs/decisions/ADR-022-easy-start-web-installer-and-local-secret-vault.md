# ADR-022 — Easy Start Web Installer and Local Encrypted Secret Vault

## Status
Accepted — 2026-09-15

## Context
The first live Phase 8 trial exposed too much operator ceremony: hand-editing `.env`, URL-encoding database credentials, running migrations/bootstrap commands, and recreating bot-runtime containers when a new env-backed Telegram token was added. The behavior was secure but unnecessarily difficult to operate for a self-hosted first trial.

## Decision
1. Add a one-command local launcher: `python3 scripts/easy_start.py`.
2. Generate missing development PostgreSQL/JWT/setup secrets locally and repair `DATABASE_URL` using URL encoding.
3. Add a one-time `/setup/` web installer guarded by a random `SETUP_CODE` plus a database singleton installation lock.
4. Verify the first Telegram token with `getMe` before creating Tenant/OWNER/Bot state.
5. Store self-hosted bot secrets in a shared encrypted local vault volume through the existing `SecretStorage` abstraction. Environment-backed references remain supported and take precedence.
6. Allow authenticated ADMIN/OWNER provisioning to accept a BotFather token directly over HTTPS and convert it immediately to an opaque vault reference. The durable provisioning job still stores only the reference; token material never enters PostgreSQL/API responses/AuditLog.
7. Resolve Mini App/Admin public URLs per tenant with process-level environment fallbacks.
8. Keep the installer locked after successful initialization. Production release gates remain unchanged.

## Security notes
- Bot tokens are accepted only over the setup request, never returned by the API, never written to AuditLog, and never stored plaintext in PostgreSQL.
- The local vault encrypts values with Fernet and restrictive file permissions. Its master-key file and ciphertext live in the same protected persistent volume, so this is appropriate for self-hosted single-node convenience but is not equivalent to independent KMS custody.
- External Vault/KMS integrations should implement the same `SecretStorage` protocol for stronger production key separation.
- `SETUP_CODE` is one-time bootstrap authorization. The database install state is the authoritative lock after initialization.

## Consequences
The common first-run path becomes one command plus one web form. New bot credentials written through the vault are immediately visible to worker/runtime processes, eliminating container recreation for those vault-backed credentials.
