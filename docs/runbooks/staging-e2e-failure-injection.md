# Staging E2E and Failure Injection

Never run this procedure against production. Use a dedicated staging database, Redis, hostname, and credentials.

1. Configure `.env` with `APP_ENV=staging`, a strong staging `JWT_SECRET_KEY`, Redis-backed rate limiting, and no production provider secrets.
2. Start the immutable stack: `docker compose up -d --build`.
3. Run `STAGING_BASE_URL=http://127.0.0.1:8010 python scripts/staging_e2e.py`.
4. For resilience verification export `APP_ENV=staging ALLOW_FAILURE_INJECTION=I_UNDERSTAND` and run `./scripts/staging_failure_injection.sh`.

The smoke flow creates/reuses a tenant isolated by slug, funds only a staging wallet, exercises bootstrap/catalog/checkout over HTTP, and proves checkout replay returns one order. Failure injection kills/restarts the worker and temporarily interrupts Redis and PostgreSQL, then reruns the smoke flow after health recovery.

A release candidate is not production-ready if the staging smoke or recovery cycle fails.
