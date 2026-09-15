# GitHub Quality Gates Runbook

## Required repository checks

Configure branch protection/rulesets for `main` so these GitHub Actions jobs are required before merge:

- `Fast verification`
- `PostgreSQL concurrency invariants`

Recommended repository settings:

1. Require a pull request before merging to `main`.
2. Require both status checks to pass.
3. Require the branch to be up to date before merge for database-sensitive changes.
4. Block force pushes and branch deletion.
5. Require review for changes under `migrations/`, `packages/payments/`, `packages/fulfillment/`, and `.github/workflows/` when repository governance supports path-based ownership.

## Local parity

Use `make verify-fast` during development and `make verify` before milestone completion. A missing PostgreSQL service is a blocked canonical gate, not permission to skip the production-engine suite.

## Failed PostgreSQL gate triage

1. Read the first failing invariant rather than rerunning blindly.
2. Confirm migrations reached the current documented head.
3. Run `alembic check`; any generated operation is schema drift and must be resolved explicitly.
4. Reproduce the single failing test with `pytest -m postgres tests/postgres/<file>::<test> -vv`.
5. Inspect transaction boundaries and database constraints before adding application-level retries.
6. Never weaken an invariant or remove a concurrency test merely to make CI green.
