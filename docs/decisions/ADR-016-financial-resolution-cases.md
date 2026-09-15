# ADR-016: Durable Financial Resolution Cases

## Status
Accepted — 2026-09-15

## Context
Provider reconciliation evidence and wallet-reversal sagas already preserve authoritative financial facts, but manual-review state was spread across `PaymentReconciliationEvent`, `WalletTopUpReversal`, and `Wallet.is_active`. An operator console needs a durable workflow without turning mutable UI state into the financial source of truth.

## Decision
Introduce `FinancialResolutionCase` as a workflow layer over immutable/provider-derived evidence. Each case is tenant-scoped, uniquely keyed to its source, may link to a payment intent, wallet, reversal, and reconciliation event, and tracks assignment, status, resolution metadata, and an optimistic `version`.

Provider evidence, ledger transactions, and reversal records are never deleted or rewritten to hide history. Automated workers create cases when a reversal or provider event enters manual review. Migration `5a6e7f8b9c10` backfills pre-existing unresolved rows.

Case mutations use row locking plus expected-version checks. STAFF+ can inspect and claim/release cases; resolution actions require ADMIN/OWNER. Wallet reactivation after a declared false positive is OWNER-only, requires a detailed note, is forbidden when a real provider reversal is linked, and fails closed if any other unresolved case or manual reversal exists for the wallet.

Supported resolution actions are intentionally narrow:

- `RETRY_LOCAL_REVERSAL`: apply the already-observed external provider reversal to the local wallet through the existing idempotent ledger boundary.
- `ACKNOWLEDGE_NO_WALLET_IMPACT`: close an unmatched evidence-only case where no wallet/reversal is linked.
- `CLOSE_KEEP_WALLET_FROZEN`: close the operator workflow while deliberately retaining the account freeze.
- `MARK_FALSE_POSITIVE_AND_UNFREEZE`: OWNER-only recovery for event-only false positives after all competing financial-review conditions are cleared.

Every claim, release, and resolution emits an `AuditLog` record.

## Consequences
The operations UI has an explicit queue with ownership and concurrency semantics while accounting remains anchored in the ledger and provider evidence. Risky shortcuts such as deleting reconciliation rows, silently reactivating a wallet, or retrying a provider refund from the browser are not exposed.
