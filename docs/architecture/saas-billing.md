# SaaS Billing Architecture

## Scope

Phase 9.2 adds the first hosted billing adapter without making hosted billing a prerequisite for GH Bot Factory. The default installation remains laptop/self-hosted friendly with `BILLING_PROVIDER=disabled`. When hosted billing is enabled, provider state is mapped into the existing Phase 9.1 `BillingEvent` convergence boundary; provider responses never directly grant entitlements in a browser request.

## Separation of responsibilities

The SaaS commercial model is intentionally split into three layers:

1. `SaaSPlan` defines product entitlements and capacity.
2. `SaaSPlanPrice` defines a non-secret recurring catalog reference for one billing provider, currency, and interval.
3. `TenantSubscription` records the normalized current commercial state after verified provider convergence.

Provider credentials are environment/vault owned. `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` are never persisted in PostgreSQL, returned through APIs, or exposed in Admin.

A provider price row is immutable with respect to provider id, amount, currency, and interval. Commercial price changes are represented by creating a new provider price and retiring the old row. This preserves historical meaning and avoids silently mutating an external billing contract.

## Tenant checkout and portal flow

Tenant ADMIN/OWNER users can request a hosted checkout or customer portal handoff from the tenant Admin console. The server resolves the authenticated tenant from `AuthenticatedPrincipal`; clients never submit an authoritative tenant id.

Checkout flow:

1. The client selects a server-listed `SaaSPlanPrice` id.
2. The API validates that the plan and price are active/public and belong to the configured provider.
3. The API refuses a second hosted checkout when a non-canceled subscription already exists.
4. A provider idempotency key is derived from tenant id, local price id, and the caller `Idempotency-Key`.
5. The adapter creates a provider-hosted checkout session containing GHBF tenant/plan/price metadata.
6. The browser receives only the short-lived provider redirect URL.
7. No local subscription mutation occurs from checkout success in the browser.

Portal flow requires an already-converged provider customer id. Plan changes, cancellation, invoices/receipts, and payment-method operations are intentionally delegated to the provider portal in Phase 9.2; the resulting provider state must return through a verified webhook or pull reconciliation before GHBF changes local subscription state.

## Signed webhook convergence

The Stripe adapter verifies `Stripe-Signature` with HMAC-SHA256, enforces a bounded timestamp tolerance, and parses only verified JSON. Relevant `customer.subscription.*` and `checkout.session.completed` events are mapped into a provider-neutral subscription snapshot.

The domain layer then:

1. resolves the external provider price to a local `SaaSPlanPrice`;
2. derives the authoritative `SaaSPlan` from that price rather than trusting browser input;
3. converges through `BillingEvent` using provider event id deduplication;
4. persists only allowlisted normalized metadata, never the raw provider payload;
5. records platform audit evidence.

Billing fingerprints canonicalize datetimes to UTC before hashing so webhook replay is stable across database drivers and timezone round trips.

## Laptop-first pull reconciliation

A laptop may intentionally have no public ingress. Hosted billing therefore cannot depend on webhooks as its only convergence path.

`scripts/platformctl.py billing-reconcile` calls the local platform API over `127.0.0.1`. The configured billing adapter fetches every provider-managed subscription, normalizes the current state, and generates a deterministic synthetic `sync:<subscription>:<hash>` event id. Re-running the same provider state is a safe duplicate.

This gives two equivalent convergence paths:

- **push:** signed provider webhook when public ingress/tunnel exists;
- **pull:** explicit localhost reconciliation when the laptop is private/offline from inbound Internet traffic.

Moving the installation to a VPS does not change the commercial domain model. The operator may enable public webhook ingress there while retaining pull reconciliation as recovery/repair tooling.

## Past-due policy

Phase 9.2 established normalized commercial state. Phase 9.3 now consumes that state through centralized server-side product entitlement gates.

- `TRIALING` / `ACTIVE`: normal commercial state.
- `PAST_DUE`: `grace_ends_at` is established from the signed event time, or from first pull observation when the webhook was missed. Repeated past-due observations preserve the original grace deadline.
- `PAUSED`: visible commercial pause state.
- `CANCELED`: terminal provider state; a future checkout can start a new subscription.
- `cancel_at_period_end=true`: the subscription remains in its provider status until the provider reports the effective cancellation.

`BILLING_PAST_DUE_GRACE_DAYS` defaults to 7. Phase 9.3 enforces the persisted deadline for growth and premium product mutations. Existing running bots are not automatically terminated when access becomes blocked, and safe contraction/security operations remain available. See ADR-028 and `docs/architecture/product-entitlements.md`.

## Trust boundaries

- Tenant JWT/RBAC can request checkout/portal for its own tenant only.
- Platform token authority owns plan/price catalog and manual subscription operations.
- Provider signature verification is required before webhook convergence.
- Provider price id, not client plan claims, maps billing state to the local plan.
- Browser redirect success is never payment/subscription authority.
- Raw webhook bodies and Stripe secrets are not durable domain data.
- Hosted billing remains optional; `BILLING_PROVIDER=disabled` leaves self-hosted operation unchanged.
