# Payment Platform Architecture

Phase 11 separates money movement from supplier/provider fulfillment. Payment adapters can observe or request external payment activity, but only the canonical payment service and ledger may credit or debit customer balances.

## Core flow

```text
Payment Method
    -> Payment Intent
    -> Provider / Manual / On-chain evidence
    -> Authenticated observation or pull reconciliation
    -> Amount + currency + identity verification
    -> exactly-once settlement gate
    -> LedgerTransaction
```

No webhook, browser return URL, screenshot, provider status string, or blockchain transaction is allowed to mutate a wallet directly.

## Payment methods and assurance

Tenant-facing methods are represented by `PaymentMethodConfig`. Provider credentials live only in `SecretStorage`; SQL stores references and public configuration.

Canonical assurance levels:

- `REGULATED_RECONCILED`: regulated/provider-authoritative reconciliation.
- `GATEWAY_VERIFIED`: authenticated gateway status with independent pull convergence.
- `ONCHAIN_VERIFIED`: installation-owned chain verifier confirms token/network/destination/amount/finality.
- `MANUAL_APPROVED`: explicit staff approval over durable evidence.

Assurance level is evidence metadata, not a guarantee that a merchant, customer, or provider satisfies every jurisdictional requirement.

## Laptop-first convergence

Public ingress is optional. `PaymentProviderReconciliationWorker` polls provider-backed open intents and `onchain_reconciliation` polls configured chain verifiers. Webhooks are fast paths only. A missed webhook must not be required for eventual convergence when the provider exposes authoritative lookup APIs.

## Provider adapters

### NOWPayments

Direct-payment integration with signed IPN support and polling. `finished` is accepted only when the paid crypto amount and pay-currency identity match the provider-created payment.

### Triple-A

Regulated-provider adapter using authenticated create + server-side status polling. Webhook/refund behavior remains fail-closed unless its current authoritative contract is explicitly implemented and tested.

### Bybit Pay

Authenticated create/query flow with merchant reference recovery for ambiguous create outcomes and RSA webhook verification. Request device/IP/browser context is transient and not persisted as financial evidence.

### Binance Pay

Authenticated create/query integration using the documented signing contract. Polling is the authoritative convergence path in the current implementation; unverified callback/refund surfaces remain disabled.

### GoZaPay (experimental)

GoZaPay has a technically useful invoice API: idempotency keys, signed HMAC-SHA256 webhook deliveries with timestamp/replay identifiers, polling, and stablecoin invoice state. GH Bot Factory intentionally classifies it as **experimental** because current public governance/documentation has unresolved consistency signals (including legal-template placeholders and cross-domain contact addresses). Operators must acknowledge this classification explicitly before enabling it.

GHBF additionally waits for GoZaPay `settled` rather than crediting on `paid`, so the provider clearing window can complete before ledger settlement. Underpayment, overpayment, review, reversal, or unknown states fail closed.

GoZaPay flexible/open-amount invoices are **not auto-credited yet**. They are stablecoin-denominated while the current commerce wallet is fiat-denominated. Auto-crediting an arbitrary USDT/USDC amount as USD would introduce an implicit FX/parity assumption. Flexible auto-credit requires an explicit multi-asset wallet/FX contract in a later economics milestone.

## Create ambiguity

Transport timeout after create can mean the provider accepted a payment but the response was lost. GHBF marks the intent `UNKNOWN` and suppresses blind create retries. Only adapters with a documented merchant-reference recovery contract may recover automatically. Provider idempotency alone is not treated as evidence that a generic recovery call cannot create a new payment.

## Reversals and refunds

Provider refund APIs are enabled only when the adapter has an authoritative current refund contract, retry semantics, and idempotency behavior. Unsupported provider refunds remain fail-closed and use the Financial Resolution workflow.

Wallet top-up reversals use the existing durable reserve/reversal saga. Financial anomalies remain represented as immutable provider/reconciliation evidence plus resolution cases rather than destructive state edits.

## On-chain verification

Self-custody verification trusts installation-owned chain configuration, never tenant-supplied RPC or token identity.

- TRON USDt: verify the configured token contract, destination, exact integer amount, successful execution, and solidified chain state.
- EVM: verify `chainId`, configured token contract, `Transfer` log, destination, exact integer amount, successful receipt, and configured finality policy.
- Transaction identities are canonicalized before uniqueness checks so casing differences cannot bypass replay protection.

## Invariants

1. Wallet credit is exactly once per canonical payment settlement.
2. Payment amount/currency comes from the server-authoritative intent plus authenticated external evidence.
3. Secrets are never returned by read APIs or persisted in provider raw payloads.
4. Provider/webhook raw data is bounded and sanitized.
5. Redirects and tenant-controlled provider API destinations are not accepted by built-in payment adapters.
6. `UNKNOWN` is preferable to duplicate credit, duplicate payment creation, or guessed financial state.
7. Operations-health code is read-only and must never settle, refund, or mutate a wallet.
