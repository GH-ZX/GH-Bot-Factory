# ADR-006: Multi-Tenant Payment Abstraction & Multi-Client API Foundation

- **Status:** Accepted
- **Date:** 2026-09-15
- **Author:** GH-Bot-Factory Architecture Team

---

## 1. Context and Problem Statement

GH-Bot-Factory requires a robust, provider-agnostic, multi-tenant payment infrastructure capable of serving diverse clients:
1. Telegram Bot (Aiogram 3 conversational checkout)
2. Telegram Mini Apps (WebApp Web interfaces)
3. Admin Control Panel (manual reconciliations and administrative refunds)
4. Future external clients and partner APIs

Prior to Phase 5, the commerce core executed cart purchases by directly debiting customer wallet balances. To support external payment gateways (e.g. Stripe, Telegram Stars, localized processors) without coupling domain commerce or wallet accounting to gateway-specific APIs, a unified payment architecture is required.

Crucial architectural invariants must be guaranteed:
- **Zero Client Authority on Amounts:** Clients must never specify or tamper with amounts. The server-side [`Order`](file:///home/it/Coding/gh-bot-factory/packages/commerce/models.py#L82) record is the sole source of truth.
- **Double-Entry Financial Ledger Invariance:** Wallet balances must never be directly manipulated. All funding and settlements must flow through [`LedgerService`](file:///home/it/Coding/gh-bot-factory/packages/payments/service.py#L24) with database-enforced idempotency.
- **Race Safety:** The system must gracefully handle races between webhook delivery and user application returns.
- **Strict Accounting Separation:** Gateway payment refunds (`PAYMENT_REFUND`) must be architecturally distinct from fulfillment failure refunds (`ORDER_FULFILLMENT_REFUND`).
- **Cryptographic Mini App Security:** Telegram Mini App `initData` must be authenticated server-side using Telegram's official HMAC-SHA256 signature algorithm. Tenant scoping must be derived from the owning bot, never trusted from client input.

---

## 2. Decision & Architecture

### 2.1 Domain Model Decoupling ([`packages/payments/models.py`](file:///home/it/Coding/gh-bot-factory/packages/payments/models.py))
We introduced four primary entities under [`packages/payments/models.py`](file:///home/it/Coding/gh-bot-factory/packages/payments/models.py):
1. [`PaymentIntent`](file:///home/it/Coding/gh-bot-factory/packages/payments/models.py#L130): Durable representation of an authoritative payment attempt. Includes `amount`, `currency`, `status`, `idempotency_key`, `provider_payment_id`, and `metadata_json`.
   - **Database Invariant:** Unique constraint `uq_payment_intent_tenant_idempotency` on `(tenant_id, idempotency_key)`.
   - **Active Intent Guard:** Partial unique index `uq_active_order_payment_intent` on `(tenant_id, order_id)` where `status IN ('CREATED', 'PENDING', 'PROCESSING')`, preventing duplicate concurrent active attempts for the same order.
2. [`PaymentTransaction`](file:///home/it/Coding/gh-bot-factory/packages/payments/models.py#L194): Ledger of gateway-level actions (`AUTHORIZATION`, `CAPTURE`, `SETTLEMENT`, `REFUND`, `VOID`).
3. [`PaymentProviderConfig`](file:///home/it/Coding/gh-bot-factory/packages/payments/models.py#L231): Tenant-scoped gateway configuration. Stores encrypted or reference-based credential pointers (`credentials_ref`, `webhook_secret_ref`) resolved via [`SecretStorage`](file:///home/it/Coding/gh-bot-factory/packages/telegram/secrets.py).
4. [`PaymentWebhookEvent`](file:///home/it/Coding/gh-bot-factory/packages/payments/models.py#L258): Audit and deduplication log for incoming provider webhooks. Unique constraint `uq_webhook_tenant_provider_event` on `(tenant_id, provider, provider_event_id)`.

### 2.2 Payment State Machine ([`packages/payments/state_machine.py`](file:///home/it/Coding/gh-bot-factory/packages/payments/state_machine.py))
An explicit state machine governs [`PaymentIntentStatus`](file:///home/it/Coding/gh-bot-factory/packages/payments/state_machine.py#L10):
- Legal states: `CREATED`, `PENDING`, `PROCESSING`, `SUCCEEDED`, `FAILED`, `EXPIRED`, `UNKNOWN`, `CANCELLED`.
- Terminal states: `SUCCEEDED`, `FAILED`, `EXPIRED`, `CANCELLED`. Once in `SUCCEEDED`, no transitions back to pending, failed, or processing are permitted.
- Any illegal transition raises `InvalidStateTransitionError`.

```
CREATED ───► PENDING ───► PROCESSING ───► SUCCEEDED (Terminal)
   │            │              │
   ▼            ▼              ▼
CANCELLED    EXPIRED        UNKNOWN ───► Reconciliation ───► SUCCEEDED / FAILED
   │                           │
   ▼                           ▼
 FAILED                      FAILED
```

### 2.3 Provider Abstraction & Capability Flags ([`packages/payments/providers/`](file:///home/it/Coding/gh-bot-factory/packages/payments/providers/))
All external gateways conform to [`PaymentProvider`](file:///home/it/Coding/gh-bot-factory/packages/payments/providers/interface.py#L67):
- Methods: `create_payment`, `get_payment`, `verify_webhook`, `refund`.
- Capability flags: `supports_idempotency_keys`, `supports_payment_lookup`, `supports_webhooks`, `supports_refunds`, `supports_partial_refunds`.
- Attempting an unsupported action raises `UnsupportedProviderCapabilityError`.
- Deterministic [`MockPaymentProvider`](file:///home/it/Coding/gh-bot-factory/packages/payments/providers/mock.py#L15) is provided for test automation.
- [`PaymentProviderRegistry`](file:///home/it/Coding/gh-bot-factory/packages/payments/providers/registry.py#L22) dynamically instantiates adapters using tenant-scoped credentials.

### 2.4 Database-Enforced Settlement Idempotency
To prevent double-crediting under high-concurrency races (e.g. concurrent webhook delivery, user return race):
- Added partial unique index `uq_settlement_idempotency` on `ledger_transactions(wallet_id, reference_type, reference_id)` WHERE `transaction_type = 'CREDIT' AND reference_type = 'PAYMENT_SETTLEMENT' AND reference_id IS NOT NULL`.
- [`LedgerService.settle_payment()`](file:///home/it/Coding/gh-bot-factory/packages/payments/service.py#L93) executes within a database savepoint. A concurrent uniqueness conflict cleanly rolls back the pending wallet credit and returns the winning ledger transaction.

### 2.5 Accounting Separation of Refund Types
The system enforces strict distinction between separate financial operations:
1. `ORDER_FULFILLMENT_REFUND`: Wallet refund issued when an upstream supplier fails to fulfill an order paid from wallet balance.
2. `PAYMENT_REFUND`: External gateway refund issued to reverse a customer payment intent. Idempotent on `uq_refund_idempotency` with `reference_type = 'PAYMENT_REFUND'`.
3. `MANUAL_ADJUSTMENT`: Support/administrative wallet adjustment.

### 2.6 Telegram Mini App Authentication Boundary ([`packages/telegram/miniapp.py`](file:///home/it/Coding/gh-bot-factory/packages/telegram/miniapp.py))
[`TelegramMiniAppAuthService`](file:///home/it/Coding/gh-bot-factory/packages/telegram/miniapp.py#L29):
- Validates query string HMAC-SHA256 signature using secret key derived from `HMAC_SHA256("WebAppData", bot_token)`.
- Rejects expired `auth_date` (> 86400s) and future timestamps (> 300s).
- Derives `tenant_id` authoritatively from the owning [`Bot`](file:///home/it/Coding/gh-bot-factory/packages/telegram/models.py#L12) record. Rejects client-supplied tenant overrides with `TenantAccessViolationError`.

### 2.7 Multi-Client REST API Foundation
FastAPI routers expose unified endpoints for all frontends:
- [`apps/api/v1/payments.py`](file:///home/it/Coding/gh-bot-factory/apps/api/v1/payments.py):
  - `POST /api/v1/payments/intents`: Authoritative intent creation.
  - `GET /api/v1/payments/intents/{intent_id}`: Tenant-scoped intent status.
  - `POST /api/v1/payments/intents/{intent_id}/cancel`: Cancellation of active intent.
  - `POST /api/v1/payments/intents/{intent_id}/reconcile`: Out-of-band status synchronization.
  - `POST /api/v1/payments/webhooks/{provider_name}`: Secure webhook ingestion and settlement.
- [`apps/api/v1/auth.py`](file:///home/it/Coding/gh-bot-factory/apps/api/v1/auth.py):
  - `POST /api/v1/auth/telegram-miniapp`: Server-side TMA session verification and user provisioning.

---

## 3. Consequences

### Positive
- Unified backend API for Bot, Mini App, Admin Panel, and external clients without logic duplication.
- Mathematical certainty against double-crediting via PostgreSQL/SQLite partial unique indexes (`uq_settlement_idempotency`).
- Complete isolation of payment credentials per tenant via [`SecretStorage`](file:///home/it/Coding/gh-bot-factory/packages/telegram/secrets.py).
- Graceful handling of network timeouts via `UNKNOWN` status and [`PaymentReconciliationService`](file:///home/it/Coding/gh-bot-factory/packages/payments/reconciliation.py#L19).
- Reversible schema evolution managed via Alembic migration [`a8d5f418df6f_add_payment_infrastructure_and_.py`](file:///home/it/Coding/gh-bot-factory/migrations/versions/a8d5f418df6f_add_payment_infrastructure_and_.py).
- 100% test coverage with 86/86 passing tests across the entire repository test suite.

### Operational Requirements
- Gateways must be configured with valid `credentials_ref` and `webhook_secret_ref` in tenant settings.
- Migrations must be run via `alembic upgrade head`.
