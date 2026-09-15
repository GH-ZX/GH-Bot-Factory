# Fulfillment Engine Architecture

## 1. Overview & Objectives

The **Fulfillment Engine** is responsible for fulfilling paid customer orders through upstream suppliers. Following **Phase 4.1 Production Hardening**, it guarantees:
1. **At-Most-Once Execution:** Strict idempotency preventing duplicate orders or multiple external charges for the same order, including crash-after-acceptance scenarios.
2. **Durable Asynchronous Processing:** Database-backed job queue (`fulfillment_jobs`) surviving application restarts and crashes with automated startup recovery.
3. **Financial Invariance & Refund Idempotency:** Absolute reconciliation between orders, supplier charges, and customer wallets. If fulfillment permanently fails, an automated refund is executed in the double-entry ledger. Duplicate refunds are strictly prevented.
4. **Transparent States & Honest Notifications:** Transient timeouts transition to `UNKNOWN` or `RETRYING` without premature failure or misleading refund notices.

---

## 2. Fulfillment Lifecycle & State Machine

```mermaid
stateDiagram-v2
    [*] --> PENDING: Order Placed & Paid
    PENDING --> PROCESSING: Worker Dispatches
    
    PROCESSING --> SUCCEEDED: Upstream returns COMPLETED
    PROCESSING --> RETRYING: Transient Rate Limit (attempt < 3)
    PROCESSING --> UNKNOWN: Network Timeout in-flight
    
    UNKNOWN --> SUCCEEDED: Reconciliation verifies Upstream COMPLETED
    UNKNOWN --> FAILED: Retries exhausted / Upstream FAILED
    
    RETRYING --> PROCESSING: Worker Retries
    
    PROCESSING --> FAILED: Non-retryable error / Max retries reached
    
    FAILED --> CompensatedRefund: Exactly-Once Ledger Refund
    CompensatedRefund --> [*]
    SUCCEEDED --> [*]
```

### FulfillmentAttempt Statuses:
- `PENDING`: Initial state upon job enqueueing.
- `PROCESSING`: In flight to the provider.
- `SUCCEEDED`: Confirmed completed upstream with external reference ID and cost amount recorded.
- `RETRYING`: Transient error occurred; scheduled for exponential backoff retry.
- `UNKNOWN`: In-flight network timeout; awaiting provider status verification or reconciliation.
- `FAILED`: Aborted due to permanent error or exhausted retries. Triggers automated ledger refund.

---

## 3. Worker Architecture & Crash Recovery

The `FulfillmentWorker` combines durable database tracking with asynchronous in-memory dispatch:

1. **Transactional Durable Storage:** Generic `enqueue()` persists a `FulfillmentJobRecord` before scheduling it in memory. Storefront checkout goes further: it stages the `QUEUED` record in the exact database transaction that records the paid order and wallet debit, so a committed debit always has durable fulfillment work.
2. **Database Polling:** The dedicated worker polls `QUEUED` rows and schedules records not already queued in that process. Discovery does not grant execution ownership.
3. **Atomic Claim:** `claim_job()` performs a conditional database update from `QUEUED` to `RUNNING`. If multiple workers discover the same record, only one claim succeeds.
4. **Crash Recovery (`recover_pending_jobs`):** When the worker restarts after an unexpected termination or server crash, it restores abandoned `QUEUED` or `RUNNING` records to the active queue.
5. **Exponential Backoff:** If an attempt fails retryably, delay is calculated as:
   $$\text{Delay} = \text{base\_backoff} \times 2^{\text{attempt} - 1}$$
6. **Dead Letter Queue:** If attempts exceed `max_retries` (default: 3), the job record is marked `DEAD_LETTER` with error details for operator inspection.

---

## 4. Double-Entry Ledger Compensation & Refund Idempotency

The platform enforces a strict accounting invariant:

> **The customer must never be charged for an order that cannot be fulfilled, and no refund may ever be credited more than once.**

When an order fulfillment encounters a terminal failure (in `FulfillmentService` or `ReconciliationService`):
1. **Order Transition:** The `Order` transitions to `OrderStatus.FAILED`.
2. **Database-Enforced Idempotent Refund:** `LedgerService.refund()` executes with unique `reference_id=str(order.id)` and canonical `reference_type=CANONICAL_REFUND_TYPE` (`"ORDER_FULFILLMENT_REFUND"`).
   - **Database-Enforced Invariant:** Refund idempotency is database-enforced. Application-level lookup is an optimization; PostgreSQL uniqueness is the authoritative concurrency guarantee.
   - **Partial Unique Index (`uq_refund_idempotency`):**
     ```sql
     CREATE UNIQUE INDEX uq_refund_idempotency
     ON ledger_transactions (wallet_id, reference_type, reference_id)
     WHERE transaction_type = 'REFUND'
       AND reference_type IS NOT NULL
       AND reference_id IS NOT NULL;
     ```
   - **Guaranteed Invariant:** `same wallet + same canonical refund reference + REFUND transaction = at most one ledger transaction`.
   - If this refund transaction already exists in the database (whether triggered by fulfillment failure or reconciliation), the existing record is returned without modifying wallet balances.
   - Under concurrent race conditions, the losing transaction hits the database partial unique index, rolls back cleanly via SQL savepoint, verifies that the requested amount matches the existing refund transaction, and returns the existing transaction without double-crediting the wallet.
   - If a duplicate refund request supplies a conflicting amount, a `LedgerIntegrityError` is raised.
3. **Customer Notification:** The customer receives a clean, user-friendly notification (`ORDER_REFUNDED`). Raw internal exception strings (`str(exc)`) are strictly excluded.

---

## 5. Reconciliation Service

For real-world distributed architectures where workers restart or network partitions obscure API responses:
- `ReconciliationService.scan_and_reconcile_tenant()` queries attempts in `PROCESSING`, `RETRYING`, or `UNKNOWN`.
- Supports querying upstream providers by `external_order_id` OR `idempotency_key` (recovering orders when the app crashed before recording the external order ID).
- If upstream completed: sets attempt `SUCCEEDED` and order `FULFILLED`.
- If upstream failed: sets attempt `FAILED`, order `FAILED`, and triggers automatic ledger refund.

---

## 6. Operator Operations Center & Manual Recovery

Phase 7.1 adds a tenant-scoped operational surface for durable fulfillment jobs and attempts.

### Visibility

STAFF and above can inspect:
- durable job state (`QUEUED`, `RUNNING`, `COMPLETED`, `FAILED`, `DEAD_LETTER`),
- the current failure classification and last error,
- manual requeue count and last requeue time,
- ordered `FulfillmentAttempt` history with attempt number, provider, external order ID, status, and error classification.

### Safe manual requeue

A manual requeue is **not** a generic replay. Before `DEAD_LETTER -> QUEUED`, the server proves all of the following:

1. The job belongs to the authenticated tenant and is currently `DEAD_LETTER`.
2. The order is still `PAID` or `PROCESSING`.
3. No canonical `ORDER_FULFILLMENT_REFUND` ledger transaction exists for the order.
4. No latest attempt has an `external_order_id`.
5. The latest attempt is not `UNKNOWN`, `PROCESSING`, or `SUCCEEDED`.
6. A `FAILED` attempt must have been explicitly marked retryable.

The transition uses a conditional database `UPDATE ... WHERE status = 'DEAD_LETTER'`. Therefore two admins clicking requeue concurrently cannot both win.

### Targeted reconciliation

ADMIN/OWNER may reconcile a single order. `ReconciliationService.reconcile_order()` only examines the requested tenant/order and never scans unrelated orders. Ambiguous `UNKNOWN` outcomes are intentionally routed here instead of being replayed.

All manual requeues and reconciliation runs are written to `AuditLog`.
