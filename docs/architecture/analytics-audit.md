# Analytics & Audit Architecture

## Purpose

Phase 7.5 provides bounded operational visibility for tenant staff without introducing a parallel accounting system. All analytics are read-only derivations from authoritative domain tables.

## Metric Semantics

| Metric | Source | Definition |
| --- | --- | --- |
| Gross order value | `orders` | Sum of order totals created in the selected period whose state proves captured commercial value: `PAID`, `PROCESSING`, `PARTIALLY_FULFILLED`, `FULFILLED`, or `REFUNDED`. |
| Refunded order value | `orders` | Sum of selected-period orders currently in `REFUNDED`. |
| Net order value | Derived | Gross order value minus refunded order value. This is **not profit** and does not subtract supplier cost, fees, taxes, or later accounting adjustments. |
| Wallet top-up value | `payment_intents` | Successful `WALLET_TOPUP` intents created in the selected period. |
| Wallet reversal value | `wallet_topup_reversals` | `COMPLETED` top-up reversals completed in the selected period. |
| Wallet liability | `wallets` | Current sum of stored wallet balances by currency. This is point-in-time and intentionally independent of the selected period. |
| Fulfillment success rate | `fulfillment_attempts` | `SUCCEEDED` attempts divided by all attempts started in the selected period. |
| Provider cost | `fulfillment_attempts` | Sum of recorded attempt cost, grouped by provider and cost currency. It is not mixed across currencies. |
| Open financial exposure counters | Cases/jobs/wallets/events | Current counts of unresolved financial cases, dead letters, frozen wallets, and reconciliation reviews. |
| Audit activity | `audit_logs` | Count/log list scoped to the tenant; logs are evidence, not mutable workflow state. |

## Currency Law

No analytics response emits a synthetic cross-currency monetary total. `USD`, `XTR`, and future currencies are separate buckets. Any future normalized reporting currency requires an explicit FX source, timestamping policy, and new ADR.

## Time Law

Analytics use UTC and bounded windows of 7, 30, or 90 days. Current-state exposure/liability counters are labeled separately from period flow metrics.

## Tenant Boundary

Every query takes `AuthenticatedPrincipal.tenant_id` and explicitly filters authoritative tables by that tenant. The browser cannot provide or override tenant context.

## Audit Exposure Boundary

Audit writers must never persist secrets. The read API additionally redacts values under keys containing secret-like terms (`secret`, `token`, `password`, `credential`, `authorization`, `api_key`, `apikey`) before returning details to the Admin client. This redaction is defense in depth and is not a substitute for safe logging.

## Performance Strategy

Migration `7b8c9d0e1f23` adds composite indexes for the bounded time-window queries introduced by Phase 7.5. No materialized read model is introduced yet. If production measurements show unacceptable query cost, the next step is an explicit read-replica/materialized-aggregate design rather than silently caching inconsistent money totals in application memory.
