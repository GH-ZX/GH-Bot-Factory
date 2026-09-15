# Wallet Top-up Architecture

## Scope

Phase 6.2 adds customer wallet funding through the existing payment gateway abstraction without manufacturing commerce orders. A top-up is represented by `PaymentIntent(purpose=WALLET_TOPUP, order_id=NULL)` and settles into the wallet ledger only after an authoritative provider success signal.

## Trust boundary

The Mini App may request an amount, currency, provider, and idempotency key. It never supplies tenant or user identity. `AuthenticatedPrincipal` supplies those values server-side.

Each enabled `PaymentProviderConfig.settings_json` may publish a top-up policy:

```json
{
  "topup_enabled": true,
  "display_name": "Gateway",
  "topup_min_amount": "5.00",
  "topup_max_amount": "500.00",
  "topup_currencies": ["USD", "EUR"]
}
```

The server validates amount precision, range, currency, configured provider, and provider adapter availability before creating the intent. An idempotency key is bound to the authenticated user, provider, amount, and currency; reuse with a different request is rejected.

## Settlement

A top-up can settle through either a verified webhook or explicit reconciliation against the provider. Both paths converge on `PaymentService.settle_payment_intent()` and `LedgerService.settle_payment()`.

The partial unique index `uq_settlement_idempotency` remains the concurrency authority. Webhook/user-return races can therefore produce at most one `PAYMENT_SETTLEMENT` credit for a payment intent.

Synchronous provider success is also supported: if `create_payment()` returns `SUCCEEDED`, settlement occurs before the create request returns.

## Checkout URL

Provider checkout URLs are stored on `PaymentIntent.checkout_url` only after server-side validation. They must use HTTPS, include a hostname, and must not contain URL credentials. The browser treats the URL as provider-controlled navigation only; it is never used as an identity or settlement signal.

## Mini App UX

The Account view exposes configured funding options, validates provider limits locally for feedback, then calls the server to create the top-up. The active intent is persisted by opaque ID in `localStorage` so the WebView can recover after a reload. While the intent is open, the client polls the reconciliation endpoint only while visible. Verified settlement refreshes the wallet balance.

## Telegram Stars native checkout

`telegram_stars` is a first-class payment provider for digital goods/services sold inside Telegram. Its configured currency is `XTR` and top-up amounts must be whole Stars. The adapter obtains the bot token through the existing secret-reference boundary and calls Telegram's Bot API to create a native invoice link. The Mini App opens that URL with `Telegram.WebApp.openInvoice` when available.

The browser callback is only UX feedback. It never settles money. The authoritative path is:

1. Telegram emits a `pre_checkout_query`. The bot runtime validates its payload, authenticated Telegram user, tenant, `PaymentIntent`, amount, currency, purpose, and current intent state before answering the query.
2. Telegram emits `successful_payment`. The bot runtime binds the actual Telegram charge ID to the stored intent and calls the same exactly-once top-up settlement boundary used by other providers.
3. Duplicate Telegram updates are harmless because `uq_settlement_idempotency` remains the ledger authority.

Tenant Stars configuration may require acceptance of an HTTPS Terms URL. Acceptance metadata (URL/version/time) is persisted with the intent for audit. `/terms` and `/paysupport` expose the tenant's customer-facing policy/support path.

## Refund and reversal saga

The legacy order-payment refund path remains intentionally blocked for `WALLET_TOPUP`. A funded-wallet refund is represented by a durable `WalletTopUpReversal` record and follows a fail-closed saga.

The critical ordering is **reserve local value before external refund**:

```text
request reversal
      |
      v
validate original settled top-up + refundable wallet balance
      |
      v
idempotent WALLET_TOPUP_REVERSAL debit (funds reserved)
      |
      v
durable worker claims reversal
      |
      v
provider refundStarPayment / provider refund
      |
      +---- definitive success ----> COMPLETED
      |
      +---- retryable/ambiguous ---> RECONCILIATION_REQUIRED
      |
      +---- integrity failure -----> MANUAL_REVIEW
```

The design deliberately does **not** automatically restore reserved wallet value after an ambiguous provider response. Doing so could create the worst financial state: an external refund succeeds while the corresponding internal value becomes spendable again. Ambiguous cases remain reserved until retry/reconciliation or operator review resolves them.

A partial unique ledger index on `(wallet_id, reference_type, reference_id)` for `WALLET_TOPUP_REVERSAL` guarantees at most one reversal debit per saga. A second request for the same funded intent resolves to the existing reversal instead of issuing another provider refund.

For Telegram Stars, a refund requires the settled Telegram payment charge ID and Telegram user ID. The provider adapter treats Telegram's already-refunded response as an idempotent success, closing the crash window where Telegram accepted the refund but the local transaction had not yet committed.

## External Telegram Stars reversal / chargeback reconciliation

Telegram can deduct Stars from the bot after the original sale without that action originating from this application's refund endpoint. The worker therefore runs a separate Stars reconciliation scanner against authenticated `getStarTransactions` history. Only outbound `user` / `invoice_payment` transactions are candidates for wallet-top-up reversal processing.

Each candidate is correlated against the authoritative tenant, Telegram charge ID, optional `ghbf:wallet-topup:<intent_id>` invoice payload, settled intent amount/currency, and the customer's Telegram identity. Every provider observation is deduplicated in `payment_reconciliation_events` by tenant/provider/transaction/event type.

If a matching `WalletTopUpReversal` already exists, the outbound transaction is treated as the provider-side evidence for the intentional refund and can complete an interrupted local saga without issuing another refund call. If no local reversal exists, the provider action is classified as an external reversal and the service creates a durable reversal record locally.

When enough wallet value remains, the external reversal is mirrored with the same database-idempotent `WALLET_TOPUP_REVERSAL` debit and completes immediately. If the customer has already spent some of the reversed value, the system cannot manufacture a negative balance under the current wallet model. It therefore freezes the affected wallet (`is_active = false`), records `MANUAL_REVIEW`, and blocks further debit/checkout use. Staff can inspect reconciliation events and, after value has been restored, call the external-reversal resolution endpoint; that performs the missing debit, closes the reversal/event, and reactivates the wallet if no other unresolved reversal exists.

Provider amount/user mismatches also freeze the wallet and remain manual-review events rather than being auto-corrected. This is deliberately fail-closed.
