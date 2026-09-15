# ADR-011: Telegram Stars External Reversal Reconciliation

- **Status:** Accepted / implemented in working tree
- **Date:** 2026-09-15

## Context

The application can initiate a Stars refund through the durable wallet-reversal saga, but Telegram documents that Stars may also be deducted from the bot after a buyer chargeback with the platform from which the buyer acquired Stars. That provider-side action is not initiated by the application's refund API.

An external reversal that is not mirrored locally leaves the customer with spendable internal wallet value even though the corresponding Stars revenue has disappeared upstream. A scanner must also distinguish this condition from an intentional refund whose provider call succeeded immediately before a local process crash.

## Decision

Run a periodic reconciliation worker over the tenant's authenticated Telegram Stars transaction history. Consider only outbound `user` / `invoice_payment` transactions and correlate them against the local `WALLET_TOPUP` PaymentIntent using the Telegram charge ID, invoice payload when available, amount, currency, tenant, and Telegram user identity.

Persist every relevant observation in `payment_reconciliation_events` with database uniqueness on tenant/provider/provider-event/event-type. This table is the durable audit and deduplication boundary for provider-side reconciliation.

If a local `WalletTopUpReversal` already exists, the outbound transaction is provider evidence for that intentional refund. The service may close a non-manual local saga without making a second refund call.

If no local reversal exists, create an externally-originated reversal. When the wallet still holds enough value, mirror the reversal through the existing idempotent `WALLET_TOPUP_REVERSAL` debit. When value has already been spent, freeze the wallet and place both the reversal and provider observation into manual review. Normal wallet debit rejects inactive wallets.

Staff may inspect tenant-scoped reconciliation events. After value has been restored, staff can resolve an externally-originated reversal locally; this performs the missing debit, marks the audit event resolved, and reactivates the wallet only if no other unresolved reversal remains. No provider refund call is made during this operation because the provider-side reversal already occurred.

## Consequences

- External chargebacks cannot silently leave the same funded value spendable in the local wallet.
- Ambiguous amount/user/provider mismatches fail closed and freeze the affected wallet.
- The transaction-history scan is bounded by tenant configuration (`transaction_scan_pages`); high-volume deployments must size this window and poll interval so provider events cannot age out between scans.
- Intentional refunds and external reversals converge on one wallet-reversal ledger invariant without being confused at the provider-action layer.

## Verification

Focused tests cover exact-once external debit, spent-value wallet freezing and later operator resolution, interrupted intentional-refund recovery without a duplicate provider call, and integrity-mismatch freezing. Migration `f02c4d8e5a63` adds the audit table and a database uniqueness boundary for top-up reversal payment transactions.
