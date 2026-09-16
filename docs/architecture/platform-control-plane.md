# Platform Control Plane Architecture

## Trust boundary

`/api/v1/platform/*` is installation-level authority and does not use tenant `AuthenticatedPrincipal` or tenant RBAC. Requests require `X-GHBF-Platform-Token`, which is compared in constant time against the local `PLATFORM_ADMIN_TOKEN` setting.

For self-hosted operation, the recommended client is `scripts/platformctl.py` over `127.0.0.1`. On a VPS, the same CLI is intended to run locally through SSH rather than exposing a browser control plane publicly.

## Plan lifecycle

The control plane can create and update `SaaSPlan` records and assign exactly one `TenantSubscription` per tenant.

- active plan: assignable to new tenants
- inactive plan: retired from new assignment
- existing subscription to an inactive plan: remains valid until explicitly migrated or cleared
- tenant Admin: read-only effective plan/usage view
- platform operator: global plan/subscription mutation authority

Plan entitlements are validated before persistence. Updating a plan also validates every existing tenant override against the proposed entitlement document so a global edit cannot silently make existing subscriptions invalid.

## Billing convergence

`BillingEvent` is the durable provider-neutral idempotency boundary.

Phase 9.2 supplies a provider-neutral adapter contract with Stripe as the first implementation. A signed webhook adapter performs provider-specific signature verification and maps the provider payload into normalized fields; a server-authenticated pull reconciliation path produces the same normalized state for laptop hosts without public ingress. The domain convergence function then:

1. fingerprints the normalized event;
2. deduplicates on `(provider, external_event_id)`;
3. rejects event-ID reuse with a different fingerprint;
4. updates/creates the tenant subscription using server-side plan lookup;
5. records the event as applied;
6. emits separate platform audit evidence.

Raw webhook payloads and provider credentials are not stored in `BillingEvent`. Checkout/customer-portal redirects are navigation only and never grant entitlements. Provider price IDs resolve through operator-owned `SaaSPlanPrice` records before a subscription plan can change.

## Audit

`PlatformAuditLog` is intentionally global and separate from tenant `AuditLog`. It records plan/price mutations, tenant subscription assignment/clear operations, billing reconciliation runs, and billing convergence decisions without storing platform credentials or secret-like metadata.
