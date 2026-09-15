# ADR-013 — Fulfillment Operator Retry Safety

## Status
Accepted — Phase 7.1

## Context

A dead-letter queue is operationally useful only if staff can inspect and recover work. A naive "retry" button is unsafe for commerce fulfillment: a timed-out provider call may have succeeded upstream even when the local worker never recorded the external order ID, and a terminal failure may already have triggered a canonical wallet refund. Replaying either case can create a duplicate supplier purchase or free fulfillment after compensation.

## Decision

1. Fulfillment jobs expose durable failure classification, manual requeue count, and last-requeue actor/time metadata.
2. STAFF+ may inspect tenant-scoped jobs and attempt history.
3. Only ADMIN/OWNER may run high-risk fulfillment mutations.
4. Manual requeue is fail-closed and atomic. It is permitted only for a `DEAD_LETTER` job when the order is still `PAID` or `PROCESSING`, no canonical fulfillment refund exists, no upstream `external_order_id` exists, and the latest attempt is not `UNKNOWN`, `PROCESSING`, or `SUCCEEDED`.
5. A terminal non-retryable provider failure cannot be manually replayed through this control.
6. The `DEAD_LETTER -> QUEUED` transition is a conditional database update so concurrent operator clicks cannot enqueue the same job twice.
7. Ambiguous provider outcomes must use targeted reconciliation first. Manual reconciliation operates on one tenant-scoped order and never scans unrelated orders as a side effect of an operator click.
8. Every requeue and manual reconciliation action is persisted in `AuditLog`.

## Consequences

The operations console deliberately refuses some seemingly convenient retries. This increases operator friction in ambiguous cases, but preserves the stronger invariant that an operator action cannot silently create a duplicate upstream order or fulfill a customer after their wallet was already refunded.
