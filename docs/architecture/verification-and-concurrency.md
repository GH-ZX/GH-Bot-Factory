# Verification and PostgreSQL Concurrency Architecture

## Verification layers

```text
Developer / Agent
       |
       v
make verify-fast
  - Ruff
  - compileall
  - handoff consistency
  - SQLite fast suite
  - Admin/Mini App JS syntax
  - git diff --check
  - pip check
       |
       v
make verify-postgres
  - alembic upgrade head
  - alembic check
  - PostgreSQL concurrency suite
       |
       v
make verify
  = fast + PostgreSQL gates
```

GitHub Actions runs the fast and PostgreSQL jobs independently so failures identify whether the problem is general code quality or database-specific behavior.

## Why PostgreSQL is a separate gate

SQLite does not reproduce PostgreSQL row locks and transaction scheduling. Tests under `tests/postgres/` are therefore not fallback copies of unit tests; they target invariants whose correctness depends on PostgreSQL behavior.

The integration database is migrated by Alembic before tests. Each PostgreSQL test truncates authoritative tables and then opens independent `AsyncSession` objects so concurrent operations use separate database connections and transactions.

## Wallet mutation serialization

All `LedgerService` balance mutations acquire the wallet row with `SELECT ... FOR UPDATE` and `populate_existing=True` before reading the authoritative balance. The latter matters when a transaction loaded the wallet before another transaction committed a mutation: after waiting for the lock, the session must refresh the object rather than reuse stale identity-map state.

This rule applies to credits, debits, settlements, top-up reversal reservations, refunds, and manual adjustments.

## Release evidence

CI uploads:

- JUnit results for the fast suite.
- JUnit results for PostgreSQL tests.
- Python/Node runtime versions.
- `pip freeze --all` dependency snapshot.

These snapshots provide run-level traceability while a resolver lockfile remains an explicit production-readiness follow-up.
