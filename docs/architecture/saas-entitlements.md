# SaaS Plans and Entitlements Architecture

## Purpose

Phase 9.0 introduces a commercial entitlement layer without coupling tenant authorization to a specific billing provider. PostgreSQL remains the authority for assigned plans/subscriptions; tenant Admin clients remain untrusted presentation surfaces.

## Domain records

### `saas_plans`

Platform-owned plan definition containing a stable key/name, activation/publication state, flexible entitlement JSON, and non-authoritative metadata.

### `tenant_subscriptions`

Exactly one current subscription row per tenant. It references a plan and stores lifecycle status, opaque external billing references, period/trial timestamps, cancellation intent, entitlement overrides, and billing metadata.

## Resolution path

`resolve_tenant_entitlements(session, tenant_id)` is the server-side policy boundary.

- No subscription: return Phase 8 `FACTORY_MAX_*` self-hosted defaults.
- Subscription present: load the referenced plan and merge explicit tenant overrides.
- Missing/invalid required limits: fail closed with `EntitlementConfigurationError`.
- `is_active=false` retires a plan from new assignment but does not revoke existing subscribers; migration/clear is an explicit platform operation.
- Tenant Admin reads map configuration failures to HTTP 503 without exposing internal plan data.

The Bot Factory provisioning and enablement paths use this resolver before capacity mutations. Browser-submitted plan or limit values are never accepted.

## Current enforced limits

- total non-deleted bots
- enabled bots
- open provisioning jobs (`PENDING`, `RUNNING`, `RETRY`)

`GET /api/v1/admin/saas/overview` exposes the authenticated tenant's effective limits and current usage for operator visibility. It does not mutate commercial state.

## Platform billing/control-plane boundary

Phase 9.1 adds a separate installation-level platform-operator authorization boundary for plan mutation and tenant assignment plus durable normalized `BillingEvent` convergence. Phase 9.2 adds a provider-neutral adapter contract, Stripe signature verification, a separate `SaaSPlanPrice` catalog, hosted checkout/portal handoff, and pull reconciliation for hosts without public ingress. Tenant Bearer JWTs are not platform authority; browser checkout results never directly authorize features. Commercial status/grace enforcement remains observe-only until Phase 9.3 centralizes product/module feature gates. See ADR-025, ADR-027, `docs/architecture/platform-control-plane.md`, and `docs/architecture/saas-billing.md`.
