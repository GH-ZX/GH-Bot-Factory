# ADR-009: Model Wallet Top-ups as Purpose-Bound PaymentIntents

## Status

Accepted — Phase 6.2 working tree.

## Context

The payment infrastructure originally required every `PaymentIntent` to reference an `Order`. Wallet funding is not a commerce order, and creating synthetic orders would couple accounting to fake catalog activity and weaken audit semantics.

## Decision

`PaymentIntent` now has an explicit purpose: `ORDER_PAYMENT` or `WALLET_TOPUP`. `order_id` is nullable only so `WALLET_TOPUP` can exist without an order. Top-ups are customer-scoped, tenant-scoped, policy-limited, idempotent, and settle through the same database-enforced payment settlement ledger boundary.

Provider checkout URLs are persisted separately and must be HTTPS. Provider success can arrive synchronously, by verified webhook, or by reconciliation; all three converge on one idempotent settlement operation.

## Consequences

- No synthetic orders are required for wallet funding.
- Existing order payment intents remain backward compatible.
- The unique active-order payment constraint continues to apply to non-null `order_id` values.
- Downgrade of the schema is refused once wallet top-up financial records exist, preventing destructive loss of financial provenance.
- Wallet top-up gateway refunds require a separate reversal design and are blocked from the legacy order-payment refund path.
