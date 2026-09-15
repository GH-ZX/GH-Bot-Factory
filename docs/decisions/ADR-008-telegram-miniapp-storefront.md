# ADR-008: Telegram Mini App Storefront and Idempotent Checkout

- **Status:** Accepted for implementation; canonical verification pending full development dependencies
- **Date:** 2026-09-15
- **Decision owners:** GH-Bot-Factory maintainers

## Context

GH-Bot-Factory already exposes a cryptographically authenticated Telegram Mini App session endpoint and a Bearer JWT authorization boundary. The next client surface needs a customer storefront that can browse a tenant catalog, inspect customer orders, and submit wallet-funded checkout requests without duplicating commerce or financial business logic in the browser.

A Telegram WebView can retry HTTP requests, a user can double-tap a checkout control, and network recovery can replay a request after the server has already committed it. Checkout therefore needs a durable idempotency boundary before the Mini App can safely initiate wallet debits.

The browser must also remain an untrusted client. It cannot be authoritative for tenant identity, user identity, prices, wallet balances, or order totals.

## Decision

### 1. The Mini App is a thin client over the existing API

The first Mini App implementation is served as static HTML, CSS, and JavaScript from FastAPI under `/miniapp/`. It does not contain commerce or financial business logic. It calls versioned REST endpoints under `/api/v1/storefront`.

The implementation intentionally has no Node/npm runtime dependency. This keeps deployment simple while the product surface is still evolving. A compiled frontend can replace the static implementation later without changing the REST trust boundary.

### 2. Telegram authentication remains server-side

The browser sends the raw `Telegram.WebApp.initData` payload and a non-secret bot identifier to the existing `POST /api/v1/auth/telegram-miniapp` endpoint. The server validates Telegram's HMAC signature and timestamp, resolves the owning `Bot` and tenant, provisions/resolves the tenant-scoped user, and returns a short-lived Bearer JWT.

The Mini App holds the access token in memory only. It does not use localStorage or sessionStorage.

### 3. Storefront identity is always derived from `AuthenticatedPrincipal`

All protected storefront endpoints depend on `get_current_principal`. The client cannot supply an authoritative `tenant_id` or `user_id`.

Customer principals can list and retrieve only their own orders. Privileged tenant roles may see tenant-wide orders through the same tenant boundary.

### 4. Catalog output is explicitly public

Only active, non-deleted tenant products and variants are returned. Product metadata and tenant settings are filtered through explicit public-key allow-lists. Internal provider metadata, credentials, arbitrary tenant settings, and internal model fields are not exposed.

### 5. Checkout uses server-authoritative prices

`POST /api/v1/storefront/checkout` accepts only variant IDs, quantities, a recipient, and an idempotency key. Pydantic request models reject unknown fields, including client-supplied price or identity fields.

`CheckoutService` reloads each `ProductVariant` through a tenant-scoped query and calculates the total from `ProductVariant.price`. A single checkout cannot mix currencies.

### 6. Wallet checkout is database-idempotent

`orders` stores:

- `checkout_idempotency_key`
- `checkout_request_hash`

A unique constraint on `(tenant_id, user_id, checkout_idempotency_key)` is the authoritative duplicate-submit guard.

The request hash is derived from the normalized recipient and sorted variant/quantity pairs. Replaying the same key with the same request returns/resumes the existing order. Reusing the key for a different request is rejected.

The order row is flushed before any wallet mutation. If the unique constraint rejects a concurrent duplicate, the request transaction is rolled back before financial work begins; the service then loads and returns the winning idempotent order. This keeps the database uniqueness boundary authoritative without relying on SQLite savepoint behavior.

### 7. Existing ledger and fulfillment laws remain authoritative

Wallet mutation continues exclusively through `LedgerService`. The Mini App does not write wallet balances or ledger transactions directly.

Storefront checkout stages a `FulfillmentJobRecord` in the same database transaction that records the paid order and wallet debit. The transaction fails closed if the durable job cannot be persisted. Supplier execution is performed later by the dedicated fulfillment worker, which polls `QUEUED` records and atomically claims each record before execution.

## API Surface

- `GET /api/v1/storefront/bootstrap`
- `GET /api/v1/storefront/catalog`
- `GET /api/v1/storefront/orders`
- `GET /api/v1/storefront/orders/{order_id}`
- `POST /api/v1/storefront/checkout`
- Static Mini App: `/miniapp/`

## Consequences

### Positive

- Telegram WebView retries cannot double-debit a wallet when they reuse the checkout idempotency key.
- The browser never becomes authoritative for identity or pricing.
- The same commerce services remain reusable by Telegram bots, the Mini App, and future admin/customer clients.
- The frontend is deployable with the API without introducing a JavaScript build pipeline.

### Trade-offs

- The first frontend is intentionally framework-free and will become harder to maintain if the UI grows substantially.
- Fulfillment is eventually consistent from the HTTP client perspective: checkout can return a paid order before the supplier completes delivery. Clients must render order status accordingly.
- Multiple worker processes may discover the same queued record, but the existing conditional database claim remains the authoritative single-executor boundary.
- The bot identifier is present in the launch URL. It is treated as routing metadata, not a credential; tenant identity is still established only after server-side Telegram signature verification.

## Verification Requirements

Before committing this milestone:

1. Apply the new Alembic migration from the previous head, downgrade it, and re-apply it.
2. Run the full Python test suite with all declared dependencies installed.
3. Run `ruff check .` with zero errors.
4. Validate the Mini App JavaScript syntax.
5. Confirm a repeated checkout key produces exactly one order and one wallet debit.
6. Confirm cross-tenant variants and cross-user customer order access are rejected.
