# ADR-010: Telegram Stars Native Payments and Durable Wallet Reversal Saga

- **Status:** Accepted / implemented in working tree
- **Date:** 2026-09-15

## Context

Phase 6.2 introduced provider-agnostic wallet funding, but a Telegram commerce product that sells digital goods/services needs a native Stars (`XTR`) payment path. A successful wallet top-up also creates a second financial problem: refunding the external payment without simultaneously controlling the internal wallet value can create money/value duplication.

The existing order-payment refund service cannot be reused because a wallet top-up has no commerce Order and its settlement credit may already have been partially spent.

## Decision

### Native Stars adapter

Register `telegram_stars` in the payment-provider registry. The adapter obtains the tenant bot token through the existing `SecretStorage` reference and calls Telegram's Bot API. It creates native invoice links, supports provider lookup/reconciliation, and performs full refunds. No bot token is persisted in `PaymentProviderConfig`.

A Stars top-up is `PaymentIntent(purpose=WALLET_TOPUP, currency=XTR)`. The bot runtime validates `pre_checkout_query` server-side and settles only from Telegram's authoritative `successful_payment` update. Mini App `openInvoice` callbacks are never a settlement authority.

### Reserve-before-refund reversal saga

Represent each funded-wallet refund as `WalletTopUpReversal`. Before contacting the external provider, debit/reserve the corresponding internal wallet value with a dedicated `WALLET_TOPUP_REVERSAL` ledger transaction. The debit is protected by a partial unique database index.

A durable worker then claims the reversal and calls the provider refund API. Provider success completes the saga. Retryable or ambiguous results transition to `RECONCILIATION_REQUIRED`; deterministic integrity failures transition to `MANUAL_REVIEW`. Reserved wallet value is not automatically returned after an ambiguous provider outcome.

Only full top-up reversals are supported in this phase. A reversal is rejected if the wallet no longer contains enough value to reserve the full amount.

## Rationale

External-refund-first can refund the customer while leaving internal value spendable if the process crashes before the wallet debit. Automatically restoring a local debit after an ambiguous external response has the inverse problem: the provider may already have refunded successfully. Reserve-before-refund with durable recovery fails closed in both cases.

Telegram's already-refunded response is treated as idempotent success so a worker retry can close the provider-success/local-commit crash window.

## Consequences

- Wallet top-up reversals are financially and semantically separate from order-payment and fulfillment refunds.
- The worker process is now responsible for both fulfillment and payment-reversal durable jobs.
- Operators have a durable record for every reversal and can identify cases needing manual review.
- External platform-originated Stars chargebacks remain outside this saga and require dedicated reconciliation (Phase 6.2.3).

## Verification

The implementation adds focused Stars/reversal tests, database migration `e91a3b7c4d52`, and full runnable regression verification. Canonical Aiogram and Ruff gates remain mandatory before commit/push when the complete development dependency environment is available.
