# ADR-005: Fulfillment Idempotency, Durable Queuing, and Financial Compensation

- **Status:** Accepted (Hardened in Phase 4.1)
- **Date:** 2026-09-15
- **Deciders:** Architecture Team

---

## 1. Context & Problem Statement

In automated commerce systems delivering digital goods via external supplier APIs, network unreliability and server crashes present severe double-spending and duplicate purchase risks:
- A network timeout may occur after the external provider accepted and charged an order, but before the response reached our application.
- Retrying without idempotency could cause the customer's wallet or our supplier account to be debited twice for a single order.
- If a server crashes while in-memory queues are running, jobs in flight could vanish, leaving paid orders stranded.
- If an order permanently fails to fulfill, failing to refund or refunding more than once breaks double-entry accounting invariants.

---

## 2. Decision Drivers

1. **At-Most-Once Execution:** Under no circumstance may a single customer order result in duplicate external fulfillment orders.
2. **Double-Entry Financial Invariant:** Every dollar debited from a user's wallet must correspond to either a confirmed fulfilled order or an audited compensatory refund.
3. **Recovery After Worker Interruption:** If a background worker or process crashes mid-fulfillment, the system must recover safely without re-purchasing already-executed orders.
4. **Honest Notifications:** Customer notifications must never state that funds have been refunded until the refund transaction is committed to the ledger.

---

## 3. Key Design Decisions

### A. Explicit Attempt-Scoped Idempotency Keys
Every fulfillment attempt generates an explicit, deterministic idempotency key:
```python
idempotency_key = f"order:{order.id}:attempt:{attempt_number}"
```
For multi-item orders, each item has a deterministic sub-key:
```python
idempotency_key = f"order:{order.id}:attempt:{attempt_number}:item:{item.id}"
```
This key is passed in headers/payload to external provider APIs (`ProviderOrderRequest.idempotency_key`). Upstream providers cache completed requests by key and replay the response if the key is seen again.

### B. Database Idempotency Guard
The `FulfillmentAttempt` entity maintains a unique composite constraint on `(idempotency_key)`. If duplicate execution is attempted concurrently within our infrastructure, the secondary attempt detects the existing record and safely replays the outcome.

### C. `UNKNOWN` & `RETRYING` State Management
When transient errors occur (such as network timeouts or rate limits):
- If the error is a timeout after dispatch, the attempt is marked `UNKNOWN`.
- If the error is a rate limit or connection failure before dispatch, the attempt is marked `RETRYING`.
- Order remains active (`PROCESSING` or `PAID`).
- No refund is issued, and no misleading "refunded" message is sent to the customer.

### D. Durable Fulfillment Queue (`FulfillmentJobRecord`)
Rather than relying purely on volatile RAM queues (`asyncio.Queue`):
- Every enqueued job is persisted to the database in `fulfillment_jobs` with status `QUEUED`.
- When picked up, status updates to `RUNNING` with `locked_at`.
- Upon crash or restart, `worker.recover_pending_jobs(session)` scans for uncompleted jobs (`QUEUED` or `RUNNING`) and safely re-enqueues them.

### E. Crash-Recovery via Reconciliation
If the application crashes after the provider accepted an order but before `external_order_id` was written to our database:
- `ReconciliationService` queries the provider using `attempt.idempotency_key`.
- The provider confirms the order was completed and returns the existing `external_order_id`.
- The attempt updates to `SUCCEEDED` and the order to `FULFILLED`, completely preventing duplicate orders.

### F. Strict Refund Idempotency
`LedgerService.refund()` enforces an idempotency check on `(wallet_id, reference_id, reference_type)`. If a refund has already been granted for an order (via worker failure or subsequent reconciliation), the existing transaction is returned without crediting the wallet twice.

---

## 4. Consequences

### Positive:
- Total financial safety: zero risk of uncompensated debits or duplicate upstream purchases.
- Workers and servers can restart or crash safely at any time with guaranteed recovery.
- Full auditability across orders, ledger transactions, and fulfillment attempts.

### Negative / Trade-offs:
- Providers that do not natively support idempotency keys require client-level synchronization (e.g. checking order status by recipient before placing a new purchase).
