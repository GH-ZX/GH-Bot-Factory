# End-to-End Order Lifecycle Architecture

## 1. Overview

This document details the end-to-end lifecycle of an order within the **GH-Bot-Factory** ecosystem, from initial Telegram user interaction through cart checkout, double-entry wallet debit, provider routing, external fulfillment, and automated financial compensation.

---

## 2. Complete Sequence Flow

```mermaid
sequenceDiagram
    autonumber
    actor Customer as Telegram Customer
    participant TG as Telegram Bot Runtime
    participant CO as CheckoutService
    participant LS as LedgerService
    participant FS as FulfillmentService
    participant PR as ProviderRouter
    participant Ext as Upstream Supplier API
    participant NS as NotificationService

    Customer->>TG: Select product variant & click "Buy"
    TG->>CO: checkout(variant_id, qty, recipient)
    
    rect rgb(240, 248, 255)
        Note over CO,LS: 1. Price Verification & Balance Debit
        CO->>CO: Query ProductVariant price from DB (never client-provided)
        CO->>LS: debit(user_wallet, total_amount, description)
        CO->>CO: Create Order(status=PAID) & OrderItem
    end

    CO->>NS: notify(PAYMENT_CONFIRMED)
    
    alt Synchronous or Worker Enqueue
        CO->>FS: execute_order_fulfillment(order_id)
        FS->>PR: route_and_execute_order(variant_id, qty, idempotency_key)
        
        PR->>Ext: create_order(request)
        
        alt Upstream Succeeds
            Ext-->>PR: ProviderOrderResponse(external_id, cost, status=COMPLETED)
            PR-->>FS: Return response
            FS->>FS: Save FulfillmentAttempt(status=SUCCEEDED)
            FS->>FS: Transition Order to FULFILLED
            FS->>NS: notify(FULFILLMENT_SUCCEEDED)
            NS-->>Customer: "🎉 Order #123 fulfilled successfully!"
        else Upstream Terminal Failure / Exhausted Retries
            Ext-->>PR: Error (e.g. out of stock / invalid recipient)
            PR-->>FS: Raise ProviderError
            FS->>FS: Save FulfillmentAttempt(status=FAILED)
            FS->>FS: Transition Order to CANCELLED
            FS->>LS: refund(user_wallet, total_amount, "Fulfillment failed")
            FS->>NS: notify(ORDER_REFUNDED)
            NS-->>Customer: "⚠️ Order #123 failed and funds were refunded to your wallet."
        end
    end
```

---

## 3. Detailed Phase Breakdown

### Phase 1: Authoritative Catalog Price Lookup
- The client or Telegram callback passes only `variant_id` and `quantity`.
- `CheckoutService` queries the authoritative database record for `ProductVariant` strictly scoped to the active `tenant_id`.
- Client-supplied prices are strictly rejected to eliminate price tampering vulnerabilities.

### Phase 2: Double-Entry Ledger Debit
- `LedgerService.debit()` checks if the customer's wallet balance covers `total_amount`.
- If insufficient balance, `InsufficientFundsError` is raised immediately before order creation.
- A new immutable debit transaction is recorded with correlation to `order_id`.

### Phase 3: Order Instantiation & State Transition
- The `Order` is saved in state `PAID`.
- An associated `OrderItem` is created with frozen unit prices.

### Phase 4: Provider Routing & Idempotent Execution
- `FulfillmentService` creates an initial `FulfillmentAttempt` record.
- The `idempotency_key` is calculated as:
  $$\text{idempotency\_key} = \text{"order:\{order.id\}:attempt:\{attempt\_number\}"}$$
- If this key was previously dispatched, the upstream client replays the recorded response, preventing duplicate purchases.
- `ProviderRouter` traverses active mappings ordered by `priority ASC`.

### Phase 5: Terminal Resolution & Financial Settlement
- **On Success:**
  - `FulfillmentAttempt.status` $\rightarrow$ `SUCCEEDED`.
  - `Order.status` $\rightarrow$ `FULFILLED`.
  - Upstream cost and supplier reference ID are committed to the database.
- **On Permanent Failure:**
  - `FulfillmentAttempt.status` $\rightarrow$ `FAILED`.
  - `Order.status` $\rightarrow$ `CANCELLED`.
  - `LedgerService.refund()` automatically restores the wallet balance with a corresponding credit ledger entry.
