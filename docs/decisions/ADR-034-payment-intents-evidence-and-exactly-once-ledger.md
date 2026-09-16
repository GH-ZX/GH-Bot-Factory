# ADR-034: Payment intents, immutable evidence, and exactly-once ledger settlement

- **Status:** Accepted
- **Date:** 2026-09-16

## Context

Phase 11 introduces manual, on-chain, gateway, and regulated-provider payment paths. Directly mapping provider callbacks to wallet balance changes would make duplicate delivery, replay, network ambiguity, and cross-provider inconsistencies financially dangerous.

## Decision

Keep `PaymentIntent` as the server-authoritative requested monetary obligation and store external proof separately as provider/webhook/on-chain/manual observations. All successful paths converge through the same settlement service and database-enforced ledger idempotency key.

Provider callbacks, blockchain observations, browser return URLs, and admin proof submissions cannot credit wallets directly. Amount/currency mismatches fail closed. Creation ambiguity enters `UNKNOWN` instead of blind retry. Laptop/self-hosted installations retain pull reconciliation as a first-class convergence path.

## Consequences

- Duplicate webhooks/polls cannot create duplicate wallet credit.
- External evidence can be audited without rewriting the intent or ledger.
- New payment providers implement transport/status mapping, not wallet mutation.
- Provider-specific refund/reversal support remains disabled until authoritative semantics are known.
