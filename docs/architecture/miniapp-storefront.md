# Telegram Mini App Storefront Architecture

## Purpose

The Telegram Mini App is a customer-facing client for the existing multi-tenant commerce platform. It is not a separate commerce engine. Authentication, tenant isolation, pricing, wallet accounting, orders, and fulfillment remain server responsibilities.

## Trust Flow

```text
Telegram client
    |
    | opens Mini App URL with bot_id routing metadata
    v
/miniapp/ static client
    |
    | raw Telegram WebApp.initData + bot_id
    v
POST /api/v1/auth/telegram-miniapp
    |
    | verify Telegram HMAC + freshness
    | resolve Bot -> Tenant
    | resolve/provision tenant-scoped user
    v
short-lived Bearer JWT
    |
    +-------------------------------+
    |                               |
    v                               v
GET /storefront/catalog       GET /storefront/bootstrap
GET /storefront/orders        POST /storefront/checkout
    |                               |
    | AuthenticatedPrincipal        | AuthenticatedPrincipal
    | tenant scoping                | tenant/user scoping
    v                               v
Commerce read models          CheckoutService
                                    |
                                    | authoritative ProductVariant.price
                                    | DB checkout idempotency
                                    v
                               LedgerService
                                    |
                                    | same DB transaction
                                    v
                           FulfillmentJobRecord
                                    |
                                    v
                           FulfillmentWorker
                                    |
                                    v
                           FulfillmentService
```


## Browser Responsibilities

The client may:

- render tenant-approved storefront branding;
- browse public catalog data;
- maintain an ephemeral cart;
- generate and reuse an idempotency key for the same checkout attempt;
- show authenticated wallet and order state returned by the server;
- integrate Telegram MainButton, BackButton, theme variables, safe areas, and haptic feedback.

The client must not:

- calculate an authoritative order total;
- provide an authoritative tenant or user ID;
- persist the Bearer token in browser storage;
- receive provider credentials or internal tenant configuration;
- update wallet balances directly.

## Storefront REST Contract

### Bootstrap

`GET /api/v1/storefront/bootstrap`

Returns the authenticated store summary, public settings, current user summary, and active wallets. A read does not create a wallet as a side effect.

### Catalog

`GET /api/v1/storefront/catalog?category_id=<optional UUID>`

Returns active, non-deleted categories, products, and active variants belonging to `principal.tenant_id`. Product metadata is filtered by a public allow-list.

### Orders

`GET /api/v1/storefront/orders`

Customers receive only their own orders. Staff/admin/owner principals remain constrained to the authenticated tenant.

`GET /api/v1/storefront/orders/{order_id}` applies the same tenant and customer-ownership rules.

### Checkout

`POST /api/v1/storefront/checkout`

Example request:

```json
{
  "items": [
    {
      "variant_id": "00000000-0000-0000-0000-000000000000",
      "quantity": 2
    }
  ],
  "recipient": "customer-recipient",
  "idempotency_key": "a-client-generated-stable-request-key"
}
```

Unknown fields are rejected. No price, amount, tenant ID, user ID, or wallet balance is accepted from the client.

## Checkout Idempotency

The Mini App creates one random idempotency key for a checkout payload and preserves it while retrying that unchanged payload. If the cart or recipient changes, it generates a new key.

The server persists the key and a SHA-256 request fingerprint on `Order`. The database unique constraint on tenant, user, and key resolves concurrent retries before a second wallet debit can occur.

```text
request A(key=K)              request B(key=K)
       |                             |
       +-------- insert Order -------+
                    |
           DB unique constraint
              /             \
         winner             loser
           |                  |
       wallet debit       load winner
           |                  |
         order <-------------+
```

Reusing `K` for a different cart or recipient is rejected.

## Deployment

Configure the Telegram Mini App/Web App URL for each bot as an HTTPS URL containing that bot's non-secret database UUID, for example:

```text
https://example.com/miniapp/?bot_id=<bot-uuid>
```

The bot UUID only selects which bot secret the server must use to validate Telegram `initData`. It is not trusted as a tenant assertion.

## Durable Fulfillment Boundary

Storefront checkout uses `execute_sync=False` and `enqueue_durable=True`. Before the financial transaction commits, checkout inserts a `FulfillmentJobRecord` with `QUEUED` status in the same session. If that insert/flush fails, the order debit is not committed.

The dedicated `apps.worker.main` process performs startup recovery, then continuously polls durable queued records. Polling is only a discovery mechanism: `FulfillmentWorker.claim_job()` remains the database-atomic execution boundary, so two worker processes discovering the same record cannot both execute it. Supplier latency and transient provider failures are therefore outside the storefront HTTP critical path.

## Wallet funding

Phase 6.2 adds an Account-view Add Funds flow backed by purpose-bound `WALLET_TOPUP` PaymentIntents. Provider policy is tenant-owned, gateway checkout URLs are HTTPS-only, and the browser treats payment status as untrusted until a verified webhook or reconciliation updates the server-side intent and exactly-once settlement ledger entry. See `docs/architecture/wallet-topups.md` and ADR-009.
