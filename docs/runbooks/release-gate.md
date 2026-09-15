# Release Gate

The canonical pre-release command is `make release-gate` with `POSTGRES_TEST_DATABASE_URL` set to a disposable PostgreSQL 17 database.

It executes the fast gate, PostgreSQL migrations and concurrency invariants, Alembic drift check, handoff consistency, secret scan, dependency integrity, Compose validation, and immutable Docker build. It writes `artifacts/release-evidence.json` with the Git SHA and migration head.

For release candidates, set `RUN_STAGING_GATE=true`. For changes to durability, payments, ledger, workers, Redis/PostgreSQL handling, or runtime lifecycle, also set `RUN_FAILURE_INJECTION=true` and run only in staging.

No agent may mark a release green solely from a prior milestone's test count.
