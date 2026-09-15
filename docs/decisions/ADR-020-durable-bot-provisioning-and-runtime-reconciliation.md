# ADR-020: Durable Bot Provisioning and Runtime Reconciliation

- **Status:** Accepted
- **Date:** 2026-09-15
- **Phase:** 8.0 + 8.4

## Context

A Bot Factory cannot depend on an operator manually inserting a `Bot` row, copying a Telegram token into the database, or restarting the runtime after every change. Provisioning crosses multiple trust and failure boundaries: tenant authorization, secret resolution, Telegram `getMe`, global Telegram bot identity, database uniqueness, worker crashes, and runtime process lifecycle.

## Decision

1. Admin requests create a durable `BotProvisioningJob`; HTTP requests never call Telegram directly.
2. The database stores only `token_secret_ref`. Plaintext Telegram bot tokens are forbidden in provisioning payloads/config and are never returned by Admin APIs.
3. A worker resolves the secret at runtime, calls Telegram `getMe`, and treats `telegram_bot_id` as the globally authoritative identity. Username is an assertion/diagnostic attribute only.
4. A Telegram bot already owned by another tenant is rejected. A same-tenant identity may be converged to the requested secret reference/config without creating a duplicate row.
5. Provisioning jobs are idempotent per `(tenant_id, idempotency_key)` and bind the key to a request fingerprint.
6. Jobs use explicit states (`PENDING`, `RUNNING`, `RETRY`, `READY`, `FAILED`, `CANCELLED`), bounded retries, leases, and stale-lease recovery.
7. Telegram/network transient failures retry with bounded exponential backoff. Invalid credentials, username mismatch, inactive tenants, and cross-tenant ownership fail closed.
8. The bot runtime periodically reconciles enabled `Bot` rows against live polling tasks. Create, update, disable, deleted state, and crashed tasks converge without process restart.
9. Runtime/API logs record error types and identifiers, never resolved token material. Startup failure history is bounded.
10. Provisioning request/ready/failure/retry/cancel and bot enable/disable actions are AuditLog-backed.

## Consequences

- Provisioning survives API/worker crashes and can be inspected/retried operationally.
- Token rotation/config changes can trigger runtime convergence without a deploy.
- The factory scales as desired state in PostgreSQL rather than process-local configuration.
- Production PostgreSQL verification remains mandatory because concurrent identity races depend on unique constraints/locking semantics.
