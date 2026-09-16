# ADR-033: Provider Offer Observations and Asynchronous Order Convergence

- **Status:** Accepted
- **Date:** 2026-09-16
- **Decision owners:** GH-Bot-Factory platform architecture

## Context

Upstream reseller suppliers expose changing price, stock, availability, and asynchronous order state. A routing decision needs normalized observations without turning a stale supplier catalog into permanent truth. Fulfillment also cannot treat a successful HTTP response as completed delivery: phone activations, account fulfillment, gift delivery, and many reseller services may remain pending for seconds or minutes.

Laptop-first installations may not have public webhook ingress, so correctness cannot depend on provider callbacks.

## Decision

1. `ProviderOfferSnapshot` stores one normalized latest observation per tenant-owned provider mapping: cost/currency, availability/stock, quantity limits, observation/expiry time, and bounded error evidence.
2. Snapshots are observations, not catalog authority. Customer-facing product/SKU identity remains in the tenant catalog.
3. Fresh unavailable observations may remove a mapping from routing eligibility. Stale observations are advisory and do not block routing.
4. Live refresh is read-only and never creates an upstream order.
5. Cost-based routing consumes fresh normalized snapshots only and follows ADR-032 currency safety.
6. Provider order responses normalize into canonical order states and delivery artifacts.
7. Only canonical `COMPLETED` may mark a fulfillment attempt/order fulfilled. `CREATED`, `PENDING`, `PROCESSING`, and `WAITING_DELIVERY` remain non-terminal; `UNKNOWN` remains ambiguous.
8. Terminal provider failure states flow through the existing durable fulfillment/refund invariants rather than bypassing them.
9. Active asynchronous attempts are reconciled by a bounded pull worker in addition to any future verified webhook path. Pull reconciliation is a first-class path so laptop/self-hosted deployments do not require public ingress.
10. The current durable `FulfillmentAttempt` correlates one upstream order identity per attempt. A multi-item order that produces more than one non-terminal upstream order is intentionally left `UNKNOWN` with explicit evidence instead of being falsely completed. Per-item upstream correlation/saga is a future hardening step before rich multi-item asynchronous supplier execution is enabled.

## Consequences

- Pending upstream responses can no longer create false fulfillment or premature refunds.
- Supplier price/availability data is useful for routing without becoming an unbounded or stale source of truth.
- Laptop deployments converge asynchronous supplier orders through polling.
- Multi-item asynchronous fulfillment remains intentionally conservative until per-item correlation is modeled durably.

## Verification

Phase 10.4/10.5 tests cover normalized offer refresh, freshness behavior, fresh-unavailable filtering, mixed-currency fail-closed routing, pending-order non-fulfillment, and pull reconciliation that later converges the same upstream order to `COMPLETED` without a webhook.
