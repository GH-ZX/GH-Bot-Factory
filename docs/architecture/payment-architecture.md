# Payment Architecture & Multi-Client API Specification

## 1. Architectural Overview

The GH-Bot-Factory Payment Infrastructure provides a provider-agnostic, multi-tenant payment framework designed to serve multiple consumer frontends:
- **Telegram Bot:** AI-guided conversational checkout.
- **Telegram Mini App (TMA):** WebApp interfaces with rich catalog browsing and checkout.
- **Admin Panel:** Merchant administration, dispute management, manual reconciliation.
- **External Clients:** Third-party partner webhooks and API integrations.

```
+------------------+    +-------------------+    +----------------+
|  Telegram Bot    |    | Telegram Mini App |    |  Admin Panel   |
+--------+---------+    +---------+---------+    +-------+--------+
         |                        |                      |
         +-------------------+    |    +-----------------+
                             |    |    |
                             v    v    v
                   +-----------------------+
                   |   FastAPI Gateway     |
                   |      /api/v1/         |
                   +-----------+-----------+
                               |
                   +-----------v-----------+
                   |    PaymentService     |
                   +-----------+-----------+
                               |
         +---------------------+---------------------+
         |                     |                     |
         v                     v                     v
+-----------------+   +------------------+   +----------------+
|  PaymentIntent  |   |  PaymentProvider |   |  LedgerService |
|  State Machine  |   |     Registry     |   |   Settlement   |
+-----------------+   +--------+---------+   +----------------+
                               |
                     +---------+---------+
                     |                   |
                     v                   v
            +-----------------+ +------------------+
            |  Mock Provider  | |  Gateway Adapter |
            +-----------------+ +------------------+
```

Core modules:
- [`PaymentIntent`](file:///home/it/Coding/gh-bot-factory/packages/payments/models.py#L130): Authoritative payment attempt bound to tenant and order.
- [`PaymentStateMachine`](file:///home/it/Coding/gh-bot-factory/packages/payments/state_machine.py#L58): Governs valid lifecycle transitions.
- [`PaymentService`](file:///home/it/Coding/gh-bot-factory/packages/payments/payment_service.py#L54): Coordinates checkout, webhooks, settlements, and gateway refunds.
- [`PaymentProvider`](file:///home/it/Coding/gh-bot-factory/packages/payments/providers/interface.py#L67): Pluggable gateway protocol.
- [`PaymentProviderRegistry`](file:///home/it/Coding/gh-bot-factory/packages/payments/providers/registry.py#L22): Factory and cache for tenant provider adapters.
- [`PaymentReconciliationService`](file:///home/it/Coding/gh-bot-factory/packages/payments/reconciliation.py#L19): Reconciles uncertain payment intents against provider gateways.
- [`LedgerService`](file:///home/it/Coding/gh-bot-factory/packages/payments/service.py#L24): Financial ledger engine enforcing settlement idempotency.
- [`TelegramMiniAppAuthService`](file:///home/it/Coding/gh-bot-factory/packages/telegram/miniapp.py#L29): Server-side cryptographic HMAC-SHA256 validator for Mini Apps.

---

## 2. Payment Intent Lifecycle & State Machine

Every checkout attempt generates a durable [`PaymentIntent`](file:///home/it/Coding/gh-bot-factory/packages/payments/models.py#L130) entity scoped strictly to `tenant_id` and `order_id`.

### State Machine Specification
```
[CREATED] ───► [PENDING] ───► [PROCESSING] ───► [SUCCEEDED] (Terminal)
   │               │               │
   ├──► [CANCELLED] ├──► [CANCELLED] ├──► [CANCELLED]
   │               │               │
   ├──► [EXPIRED]   ├──► [EXPIRED]   ├──► [EXPIRED]
   │               │               │
   └──► [FAILED]    ├──► [FAILED]    ├──► [FAILED]
                   │               │
                   └──► [UNKNOWN] ◄┘
                           │
                 (Reconciliation Service)
                           │
                    [SUCCEEDED] / [FAILED]
```

### Invariants:
1. **Server-Authoritative Amounts:** `PaymentIntent.amount` and `PaymentIntent.currency` are derived directly from [`Order.total_amount`](file:///home/it/Coding/gh-bot-factory/packages/commerce/models.py#L106) and [`Order.currency`](file:///home/it/Coding/gh-bot-factory/packages/commerce/models.py#L105). Client-submitted amounts are strictly rejected.
2. **At Most One Active Intent:** Partial unique index `uq_active_order_payment_intent` ensures only one intent in `('CREATED', 'PENDING', 'PROCESSING')` can exist per `(tenant_id, order_id)`.
3. **Immutability of Terminal Succeeded State:** Once settled (`SUCCEEDED`), an intent cannot be moved back to `PENDING`, `PROCESSING`, or `FAILED`.

---

## 3. Webhook Processing & Security

Webhooks represent asynchronous gateway callbacks informing the platform of payment settlement, authorization, or failure.

### Processing Pipeline:
```
Incoming Webhook
      │
      ▼
1. Resolve Tenant Gateway Config (PaymentProviderConfig)
      │
      ▼
2. Verify Cryptographic Signature (HMAC-SHA256 / Provider Public Key)
      │  (Fails ➔ WebhookVerificationError, 401 Unauthorized)
      ▼
3. Compute SHA256 Payload Hash & Deduplicate (uq_webhook_tenant_provider_event)
      │  (Duplicate ➔ Return existing PaymentWebhookEvent, idempotent 200)
      ▼
4. Load Tenant-Scoped PaymentIntent
      │
      ▼
5. Authoritative Verification (Amount == Intent.amount, Currency == Intent.currency)
      │  (Mismatch ➔ PaymentIntegrityError, 409 Conflict)
      ▼
6. State Machine Transition & Ledger Settlement
      │
      ▼
7. Mark Webhook Processed (processed=True, processed_at=now)
```

### Webhook-First Race Handling:
If the webhook arrives and settles the payment before the user's browser or Mini App redirects back to the application:
1. The webhook transitions `PaymentIntent.status` to `SUCCEEDED` and executes [`LedgerService.settle_payment()`](file:///home/it/Coding/gh-bot-factory/packages/payments/service.py#L93).
2. When the user later returns and calls the return/settlement endpoint, `PaymentService.settle_payment_intent()` detects that the intent is already `SUCCEEDED` and returns the existing transaction without executing a duplicate credit.
3. Conversely, if the user returns first, the subsequent webhook detects that the intent is settled, and `LedgerService.settle_payment()` enforces single credit via database partial unique index `uq_settlement_idempotency`.

---

## 4. Ledger Settlement & Double-Entry Invariance

Ledger settlement converts external fiat or crypto payment into internal accounting reality:
- **Ledger Invariant:** Exactly one successful payment settlement corresponds to exactly one [`LedgerTransaction`](file:///home/it/Coding/gh-bot-factory/packages/payments/models.py#L73) credit.
- **Database Enforced Idempotency:**
  ```sql
  CREATE UNIQUE INDEX uq_settlement_idempotency 
  ON ledger_transactions(wallet_id, reference_type, reference_id)
  WHERE transaction_type = 'CREDIT' 
    AND reference_type = 'PAYMENT_SETTLEMENT' 
    AND reference_id IS NOT NULL;
  ```
- **Atomicity via Savepoints:** [`LedgerService.settle_payment()`](file:///home/it/Coding/gh-bot-factory/packages/payments/service.py#L93) wraps the wallet credit in a nested transaction. If a concurrent worker hits the index collision, the balance modification is cleanly rolled back and the winning transaction is returned.

---

## 5. Accounting Distinction: Gateway vs Fulfillment Refunds

| Attribute | Order Fulfillment Refund | Payment Gateway Refund |
| :--- | :--- | :--- |
| **Canonical Type** | `ORDER_FULFILLMENT_REFUND` | `PAYMENT_REFUND` |
| **Trigger** | Upstream supplier fulfillment failure | Merchant cancellation / customer dispute |
| **Funds Source** | Internal merchant wallet liability | External payment gateway reversed funds |
| **Idempotency Identity** | `order_id` | `payment_intent_id` |
| **Database Index** | `uq_refund_idempotency` | `uq_refund_idempotency` |

---

## 6. Telegram Mini App Authentication Boundary

Telegram Mini Apps transmit session authentication via `window.Telegram.WebApp.initData`.

### Validation Algorithm:
1. Parse URL-encoded query parameters.
2. Extract the received `hash`.
3. Alphabetically sort remaining key-value pairs separated by newlines (`data_check_string`).
4. Generate `secret_key = HMAC_SHA256(b"WebAppData", bot_token)`.
5. Compute `computed_hash = HMAC_SHA256(secret_key, data_check_string)`.
6. Assert `hmac.compare_digest(computed_hash, received_hash)`.
7. Enforce timestamp freshness: `now - auth_date <= 86400s` and `auth_date <= now + 300s`.
8. Resolve `tenant_id` from the registered [`Bot`](file:///home/it/Coding/gh-bot-factory/packages/telegram/models.py#L12) entity. The client cannot forge or supply `tenant_id`.

---

## 7. Multi-Client Unified REST API Specification

All frontends interface through the standardized FastAPI routes defined under [`apps/api/v1/payments.py`](file:///home/it/Coding/gh-bot-factory/apps/api/v1/payments.py) and [`apps/api/v1/auth.py`](file:///home/it/Coding/gh-bot-factory/apps/api/v1/auth.py):

| Method | Endpoint | Required Headers | Request Body | Status Code | Purpose |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `POST` | `/api/v1/payments/intents` | `X-Tenant-ID`, `X-User-ID` | `CreatePaymentIntentRequest` (`order_id`, `provider_name`, `idempotency_key`, `metadata`, `return_url`) | 201 Created | Creates intent with server-authoritative amount from `Order` |
| `GET` | `/api/v1/payments/intents/{intent_id}` | `X-Tenant-ID` | None | 200 OK | Fetches intent status, strictly scoped to tenant |
| `POST` | `/api/v1/payments/intents/{intent_id}/cancel` | `X-Tenant-ID` | None | 200 OK | Cancels active non-terminal intent |
| `POST` | `/api/v1/payments/intents/{intent_id}/reconcile` | `X-Tenant-ID` | None | 200 OK | Reconciles intent against provider gateway |
| `POST` | `/api/v1/payments/webhooks/{provider_name}` | `X-Tenant-ID` | Raw gateway webhook payload (JSON/bytes) | 200 OK | Authenticates webhook signature, dedupes, and settles |
| `POST` | `/api/v1/auth/telegram-miniapp` | Optional `X-Tenant-ID` | `TelegramMiniAppAuthRequest` (`init_data`, `bot_id`, `expected_tenant_id`) | 200 OK | Validates TMA HMAC signature and binds customer |

---

## 8. Database Invariants & Concurrency Constraints

The database schema enforces financial and concurrency guarantees at the engine level:

1. **Payment Intent Idempotency:**
   `uq_payment_intent_tenant_idempotency` on `payment_intents(tenant_id, idempotency_key)`
2. **Active Order Payment Intent Uniqueness:**
   `uq_active_order_payment_intent` on `payment_intents(tenant_id, order_id)` WHERE `status IN ('CREATED', 'PENDING', 'PROCESSING')`
3. **Webhook Deduplication Uniqueness:**
   `uq_webhook_tenant_provider_event` on `payment_webhook_events(tenant_id, provider, provider_event_id)`
4. **Ledger Settlement Idempotency:**
   `uq_settlement_idempotency` on `ledger_transactions(wallet_id, reference_type, reference_id)` WHERE `transaction_type = 'CREDIT' AND reference_type = 'PAYMENT_SETTLEMENT' AND reference_id IS NOT NULL`
5. **Ledger Refund Idempotency:**
   `uq_refund_idempotency` on `ledger_transactions(wallet_id, reference_type, reference_id)` WHERE `transaction_type = 'REFUND' AND reference_type IS NOT NULL AND reference_id IS NOT NULL`

---

## 9. Verification & Migration Safety

- **Migration:** Reversible Alembic migration [`migrations/versions/a8d5f418df6f_add_payment_infrastructure_and_.py`](file:///home/it/Coding/gh-bot-factory/migrations/versions/a8d5f418df6f_add_payment_infrastructure_and_.py).
- **Test Suite:** 86/86 passing tests covering all payment intent lifecycles, webhook-first races, concurrent settlements, TMA HMAC validation, tenant scoping, and API endpoints.

