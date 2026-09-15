# ADR-021: Versioned Bot Templates and Verified Per-Bot Branding Context

- **Status:** Accepted
- **Date:** 2026-09-15

## Context

Durable provisioning (ADR-020) created verified Telegram bot instances, but the Admin still accepted raw bot config JSON and the storefront read branding primarily from tenant settings. That model is difficult to operate safely at scale: configuration shape can drift, users can create invalid combinations, and two bots owned by one tenant cannot reliably present distinct brands.

## Decision

1. Bot templates are code-defined, immutable/versioned configuration baselines. A provisioning request names a template key/version plus validated overrides; the server builds the persisted configuration.
2. The Admin wizard never needs raw JSON for the normal provisioning path. The legacy config contract remains temporarily supported for compatibility, but the wizard uses the template contract exclusively.
3. Persisted bot config carries `_factory.template_key` and `_factory.template_version` so an agent/operator can reconstruct provenance without a database join or mutable global template row.
4. Branding overrides are allow-listed and validated (lengths, hex accent, HTTPS URLs without credentials, Telegram support handle, known modules/currency/locale formats).
5. Telegram Mini App authentication adds the verified internal `bot_id` as a signed JWT claim. Storefront branding is then resolved from that server-verified bot, never from a browser-selected authoritative bot ID.
6. Bot-specific public branding overrides tenant-level storefront defaults. Non-public bot config is not returned by storefront bootstrap.
7. Runtime menu text and Telegram `/start` store-button wording are read from the bot config, so one tenant can operate differently branded bots without forks.
8. Post-provision configuration changes preserve the credential reference and are tenant-scoped, ADMIN/OWNER-only, audited, and picked up by runtime reconciliation.

## Consequences

- New templates require an explicit version bump when their defaults change materially.
- Existing bots are not silently rewritten when a template evolves.
- The server remains the source of truth for configuration validity.
- Per-bot white-label behavior is possible without separate deployments.
- A future Phase 8.7 rollout/version system can build on the persisted template provenance.
