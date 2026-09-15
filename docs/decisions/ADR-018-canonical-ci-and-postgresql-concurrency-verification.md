# ADR-018: Canonical CI and PostgreSQL Concurrency Verification

- **Status:** Accepted
- **Date:** 2026-09-15
- **Phase:** 7.9.1–7.9.2 Production Readiness

## Context

The fast repository suite historically used in-memory SQLite. That provides excellent feedback speed but cannot prove PostgreSQL row-locking, partial-index, transaction-blocking, or concurrent-worker behavior. The system now contains money movement, durable fulfillment, payment settlement, refunds/reversals, and operator concurrency boundaries where a SQLite-only green suite is insufficient.

The repository also had no executable GitHub Actions quality gate, so the documented verification law depended on manual execution and environment memory.

## Decision

1. The canonical release gate is split into a **fast gate** and a **PostgreSQL gate**.
2. GitHub Actions must execute both gates for pushes to `main` and pull requests.
3. PostgreSQL integration tests are marked `postgres` and require `POSTGRES_TEST_DATABASE_URL` using `postgresql+asyncpg://`.
4. The PostgreSQL gate must run migrations to head, run `alembic check`, and then execute the concurrency suite against a real PostgreSQL service.
5. Every wallet balance mutation is serialized with `SELECT ... FOR UPDATE` on the authoritative wallet row before computing `balance_before`/`balance_after`.
6. CI records JUnit output plus a dependency/runtime snapshot as build evidence.
7. `scripts/verify.sh` and the root `Makefile` are the canonical local entry points; agents should not invent ad-hoc partial release commands.
8. A handoff-consistency checker is part of the fast gate so working-tree state and agent documentation cannot silently diverge.

## Concurrency invariants initially covered

- Two stale readers cannot overspend one wallet.
- Duplicate concurrent checkout with one idempotency key produces one order, one checkout debit, and one durable fulfillment job.
- Concurrent settlement of the same PaymentIntent credits the wallet exactly once.
- A durable fulfillment job has exactly one successful claimant.
- Concurrent wallet creation converges to one `(tenant_id, user_id, currency)` row.

## Migration drift cleanup

The original payment-infrastructure migration created four secondary indexes on primary-key `id` columns even though current ORM metadata does not declare them. `PaymentTransaction.provider_tx_id` also declared both an explicit named index and `index=True` in ORM metadata. Migration `8a9b0c1d2e34` removes the redundant primary-key indexes, and the ORM retains only the explicit provider transaction index. This makes `alembic check` a meaningful mandatory gate instead of accepting permanent known drift.

## Consequences

### Positive

- Database-sensitive safety claims are tested against the production database engine.
- Wallet mutation races are serialized at the authoritative row, not left to application timing.
- CI becomes the shared verification contract for humans and agents.
- Migration drift becomes a release-blocking defect rather than documentation debt.

### Trade-offs

- PostgreSQL tests are slower than SQLite and require a service container.
- `FOR UPDATE` serializes concurrent balance changes for one wallet by design; correctness is preferred over maximum per-wallet write throughput.
- Full dependency reproducibility still requires a committed resolver lockfile. Until that is added, CI captures `pip freeze` so every run remains traceable but not perfectly reproducible.
