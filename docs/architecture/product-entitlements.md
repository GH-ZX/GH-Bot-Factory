# Product Entitlements and Commercial Access

## Policy boundary

`packages/saas/product_entitlements.py` is the Phase 9.3 commercial authorization boundary. It composes the plan/override document from `packages/saas/service.py` with normalized `TenantSubscription` status from the billing domain.

The resulting decision is independent of any billing provider. Stripe, a future provider, or manual platform assignment all converge into the same normalized subscription fields before this policy runs.

## Commercial access states

- `SELF_HOSTED`: no subscription row; all existing GH Bot Factory capabilities remain available.
- `ACTIVE`: `ACTIVE` or `TRIALING` subscription.
- `GRACE`: `PAST_DUE` with a verified future `grace_ends_at`.
- `BLOCKED`: expired/missing past-due grace, `PAUSED`, or `CANCELED`.

A blocked subscription does not automatically terminate an already running bot. Phase 9.3 blocks growth and premium mutations while leaving safe contraction/security actions available.

## Initial feature registry

- `custom_branding`: arbitrary per-bot branding overrides beyond the selected template defaults.
- `canary_rollout`: moving a bot from STABLE to CANARY. Returning CANARY to STABLE remains allowed as a safe contraction action.
- `runtime_controls`: manual runtime restart requests.

Unknown future feature keys remain valid in plan JSON and can be added to the centralized registry without schema migration.

## Bot Factory enforcement

Server-side enforcement currently covers:

- new bot provisioning: requires current commercial access;
- manual retry of failed provisioning: requires current commercial access;
- enabling a disabled bot: requires current commercial access;
- disabling a bot: always allowed;
- custom branding mutation: requires `custom_branding`;
- STABLE -> CANARY: requires `canary_rollout`;
- CANARY -> STABLE: always allowed;
- runtime restart: requires `runtime_controls`;
- credential verify/rotate: remains available as a security/recovery operation.

`GET /api/v1/admin/saas/overview` returns both configured plan feature flags and `effective_features`, plus the commercial access state/reason/grace deadline. Browser state is presentation only.
