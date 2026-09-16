# ADR-027 — Provider-Agnostic SaaS Billing with Push/Pull Convergence

- **Status:** Accepted
- **Date:** 2026-09-15
- **Decision scope:** Phase 9.2 hosted billing, tenant checkout/portal, and laptop-first reconciliation

## Context

GH Bot Factory can run primarily on a laptop and may later move to a VPS. A traditional SaaS billing design often assumes permanent public webhook ingress and couples internal product plans directly to one provider's price/subscription objects. That would make a private laptop installation second-class and make provider migration expensive.

The Phase 9.1 platform control plane already provides provider-neutral `SaaSPlan`, `TenantSubscription`, and durable `BillingEvent` convergence boundaries.

## Decision

1. Keep entitlements (`SaaSPlan`) separate from provider commercial catalog references (`SaaSPlanPrice`).
2. Keep billing secrets exclusively in environment/vault configuration; persist only non-secret provider ids and normalized state.
3. Introduce a billing adapter protocol and ship Stripe as the first adapter. Core convergence consumes provider-neutral subscription snapshots.
4. Tenant checkout and customer portal are redirect handoffs only. Browser success never writes subscription state.
5. Signed webhooks converge through the existing `BillingEvent` idempotency boundary.
6. Provide pull reconciliation through the localhost platform control plane so a laptop without inbound Internet access can converge provider state on demand.
7. Define a deterministic past-due grace deadline but keep commercial enforcement observe-only in Phase 9.2. Product gating is deferred to Phase 9.3.
8. Treat price amount/currency/interval/provider ids as immutable commercial references; changes create a new price row and retire the old one.

## Consequences

- A VPS can use normal webhook ingress while a laptop can remain private and use reconciliation.
- Billing-provider replacement is localized to adapter/catalog mapping rather than entitlement logic.
- Provider portals handle plan change/cancel/payment-method/invoice UX initially, reducing sensitive PCI/payment UI surface in GHBF.
- Missed webhooks are recoverable without manually editing subscription rows.
- Commercial status does not unexpectedly disable a working self-hosted tenant until Phase 9.3 adds explicit, reviewed feature-gate semantics.
