# ADR-026: Laptop-First Portable Self-Hosted State

- **Status:** Accepted
- **Date:** 2026-09-15
- **Decision scope:** Self-hosted laptop operation and later Laptop -> VPS migration

## Context

GH Bot Factory may run for an extended period on a personal laptop before being moved to a VPS. PostgreSQL alone is not sufficient for a faithful move because bot/provider credentials live in the encrypted local secret vault. Redis contains coordination/ephemeral state and is deliberately not authoritative.

Copying Docker volumes manually is error-prone and host-specific. Copying `.env` is also undesirable because database credentials, JWT signing material, local platform credentials, and public URLs should be destination-owned.

## Decision

1. PostgreSQL remains the authoritative durable business state.
2. The encrypted local secret vault, including its master key, is the second required portable state component.
3. Redis data is excluded from portable bundles. Durable queues and financial/business state must recover from PostgreSQL; Redis heartbeats/rate-limit/observational state is rebuildable.
4. Portable exports quiesce API/worker/bot-runtime mutation services before taking the PostgreSQL dump and vault snapshot, then restore the previously running services.
5. Portable bundles contain:
   - PostgreSQL custom-format dump
   - encrypted secret-store archive (ciphertext plus vault master key)
   - format/version manifest
   - SHA-256 checksums
6. `.env` is intentionally excluded. The destination host owns its database password, JWT secret, platform admin token, public URLs, proxy settings, and host-specific configuration.
7. Because the bundle contains the vault master key, plaintext export is denied by default. GPG encryption is preferred; plaintext requires explicit operator opt-in.
8. Import is destructive and requires an explicit `--confirm` flag. It restores PostgreSQL, restores the vault, runs Alembic migrations, and then resumes services that were running before import.
9. Archive extraction rejects path traversal, links, and unsupported archive members.
10. The portable format is versioned as `ghbf-portable-state-v1` so future migrations can detect incompatible bundle layouts.

## Consequences

- A laptop can be the primary host without creating a dead-end deployment topology.
- Moving to a VPS does not require re-entering BotFather/provider secrets.
- Existing browser/API sessions may be invalidated after a move because the destination may generate a new JWT signing secret; this is intentional and safe.
- Public URLs are reconfigured for the destination instead of being blindly copied from the source laptop.
