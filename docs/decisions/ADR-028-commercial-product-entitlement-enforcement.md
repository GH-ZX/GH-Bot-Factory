# ADR-028: Centralized Commercial Product Entitlement Enforcement

- **Status:** Accepted
- **Date:** 2026-09-15

## Context

Phase 9.2 made normalized billing status trustworthy, but intentionally left it observe-only. Enforcing subscription state independently inside individual UI surfaces would create inconsistent grace behavior, client-side authorization gaps, and risk breaking laptop/self-hosted installations that intentionally have no hosted subscription.

## Decision

Introduce one server-side product-entitlement policy boundary.

1. A tenant with no `TenantSubscription` is explicitly `SELF_HOSTED` and keeps all existing product capabilities.
2. `ACTIVE` and `TRIALING` subscriptions permit plan-entitled commercial capabilities.
3. `PAST_DUE` permits them only until the persisted `grace_ends_at` deadline. Missing or expired grace fails closed.
4. `PAUSED` and `CANCELED` are commercially blocked.
5. Plan feature flags are server authorization, not UI preferences. Phase 9.3 initially gates `custom_branding`, `canary_rollout`, and `runtime_controls`.
6. Commercially blocked tenants cannot create/retry provisioning work or re-enable bots, but safe contraction/recovery operations such as disabling a bot and credential security actions remain available.
7. Existing workloads are not automatically terminated when billing becomes blocked; destructive shutdown automation is deliberately excluded.
8. The Admin UI may reflect effective capabilities, but API/domain enforcement remains authoritative.

## Consequences

- Self-hosted laptop operation remains backward compatible and does not require a fake subscription.
- Billing grace semantics are deterministic across API surfaces.
- Premium feature rollout can expand through a central registry without scattering provider/status logic.
- Support/operator tooling can distinguish configured plan flags from effective current access.
