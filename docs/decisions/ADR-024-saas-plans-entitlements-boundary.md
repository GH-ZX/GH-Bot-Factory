# ADR-024: SaaS Plans, Subscriptions, and Entitlement Authority

- **Status:** Accepted
- **Date:** 2026-09-15
- **Decision scope:** Phase 9.0 productization foundation

## Context

Phase 8 enforced Bot Factory capacity from process environment settings. That is appropriate for a self-hosted deployment but cannot represent per-tenant commercial plans in a multi-tenant SaaS deployment. Moving directly to a billing-provider SDK would also couple core authorization and capacity decisions to an external provider and browser-controlled checkout state.

## Decision

1. `SaaSPlan` is the platform-owned commercial capability definition. Its `entitlements` object contains server-authoritative limits and feature flags.
2. A tenant may have at most one current `TenantSubscription`. It records the selected plan, lifecycle status, opaque billing-provider identifiers, period/trial metadata, and explicit entitlement overrides.
3. When a `TenantSubscription` exists, its referenced plan is authoritative for the tenant's Bot Factory limits. Invalid or incomplete subscribed-plan limits fail closed. Phase 9.1 later clarifies that plan inactivity means retired from new assignment, not immediate revocation of existing subscribers.
4. When no subscription exists, the resolver returns the existing Phase 8 environment-configured Bot Factory limits. This is an explicit self-hosted compatibility path, not a SaaS entitlement bypass.
5. Tenant Admin APIs may read only their own effective plan, usage, limits, and feature flags. They cannot create plans, assign subscriptions, change billing identifiers, or grant entitlement overrides.
6. Future billing-provider adapters must converge external subscription events into this domain model through a platform control-plane boundary. Browser state and provider checkout redirects are never entitlement authority.
7. Billing-provider webhook ingestion must be durable and idempotent before it is allowed to mutate subscription state.

## Entitlement contract

Phase 9.0 requires these integer plan keys:

- `max_bots`
- `max_enabled_bots`
- `max_open_provisioning_jobs`

All must be positive integers and `max_enabled_bots <= max_bots`. Optional `features` must be a string-to-boolean object. Tenant overrides merge explicitly on top of the plan and are subject to the same validation.

## Consequences

- Bot Factory capacity is no longer globally uniform once a tenant is subscribed.
- Self-hosted deployments remain backward compatible without creating synthetic subscriptions.
- A billing provider can be selected later without rewriting Bot Factory authorization logic.
- Plan mutation and assignment require a new platform-operator trust boundary; tenant RBAC is deliberately insufficient.
- Subscription status is persisted and visible in Phase 9.0, but commercial status enforcement semantics (grace periods, past-due behavior, cancellation effective dates) are deferred to the billing control-plane milestone.
