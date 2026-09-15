# ADR-005: Fulfillment Idempotency and Financial Compensation Mechanics

- **Status:** Accepted
- **Date:** 2026-09-15
- **Deciders:** Architecture Team

---

## 1. Context & Problem Statement

In automated commerce systems delivering digital goods via external supplier APIs, network unreliability presents severe double-spending and duplicate purchase risks:
- A network timeout may occur after the external provider accepted and charged an order, but before the response reached our application.
- Retrying without idempotency could cause the customer's wallet or our supplier account to be debited twice for a single order.
- If an order permanently fails to fulfill, failing to return funds breaks accounting invariants and leads to customer disputes.

---

## 2. Decision Drivers

1. **At-Most-Once Execution:** Under no circumstance may a single customer order result in duplicate external fulfillment orders.
2. **Double-Entry Financial Invariant:** Every dollar debited from a user's wallet must correspond to either a confirmed fulfilled order or an audited compensatory refund.
3. **Recovery After Worker Interruption:** If a background worker or process crashes mid-fulfillment, the system must recover safely without re-purchasing already-executed orders.

---

## 3. Key Design Decisions

### A. Explicit Attempt-Scoped Idempotency Keys
Every fulfillment attempt generates an explicit, deterministic idempotency key:
```python
idempotency_key = f"order:{order.id}:attempt:{attempt_number}"
```
This key is passed in the headers or payload to external provider APIs (`ProviderOrderRequest.idempotency_key`). Upstream providers cache completed requests by key and replay the response if the key is seen again.

### B. Database Idempotency Guard
The `FulfillmentAttempt` entity maintains a unique composite constraint on `(order_id, attempt_number)`. If duplicate execution is attempted concurrently within our infrastructure, the secondary attempt detects the existing record and safely replays the outcome.

### C. Automated Ledger Refund Compensation
When fulfillment reaches a permanent failure state (e.g. non-retryable provider error or max retry limit reached):
1. The `Order` transitions to `CANCELLED`.
2. `FulfillmentService` immediately executes `LedgerService.refund(wallet, order.total_amount, description)`.
3. The refund writes an immutable credit transaction to the user's wallet referencing the order ID.
4. The user is notified that the order could not be fulfilled and their balance was restored.

---

## 4. Consequences

### Positive:
- Total financial safety: zero risk of uncompensated debits or duplicate upstream purchases.
- Workers can safely retry transient errors without fear of double-charging.
- Full auditability across orders, ledger transactions, and fulfillment attempts.

### Negative / Trade-offs:
- Providers that do not natively support idempotency keys require client-level synchronization (e.g. checking order status by recipient before placing a new purchase).
