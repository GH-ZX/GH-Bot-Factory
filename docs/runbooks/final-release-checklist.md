# Final Release Qualification Checklist

Use this after feature development is complete. A code-complete snapshot is not a production release until the environment-specific evidence below is collected.

## Local/static gate

```bash
make verify-fast
python3 scripts/check_handoff_consistency.py
python3 scripts/doctor.py
```

Expected: no blocking failures.

## PostgreSQL concurrency gate

Point `POSTGRES_TEST_DATABASE_URL` at an isolated PostgreSQL test database and run:

```bash
make verify-postgres
```

Do not point destructive tests at the live production database.

## Docker/release gate

```bash
make release-gate
```

Archive the emitted release evidence together with the release Git SHA and migration head.

## Staging

Exercise at least:

- first-run/setup or an isolated staging tenant;
- bot provision/verify/rotate/restart;
- advanced business wizard for a reseller bot;
- provider connection health and one sandbox supplier order;
- checkout, wallet debit/hold/release, fulfillment completion;
- payment duplicate/replay/idempotency paths;
- worker restart during pending payment/provider reconciliation;
- deliberate provider timeout → `UNKNOWN` → reconciliation;
- backup, restore, and portable-state move;
- public HTTPS Mini App/Admin if those surfaces will be used.

## Real integration smoke tests

For each payment/provider enabled in production, perform the smallest safe real transaction supported by the vendor. Verify amount, currency/asset, external identity, finality/status, exactly-once ledger effect, fulfillment, and audit evidence.

## Go/No-Go

Go only when:

- all canonical gates are green;
- no unresolved financial `UNKNOWN`/reversal cases exist from testing;
- backup restore is proven;
- required credentials are in the encrypted vault/environment, never committed;
- public ingress trusts only the intended reverse proxy/tunnel;
- enabled adapters have current authoritative API documentation and sandbox/live evidence.
