# Agent Change Log

This is the concise chronological handoff log for coding agents. It complements, but does not replace, ADRs or the verbatim prompt history.

## 2026-09-15 — Phase 7.5 Analytics & Audit

- Added `packages.analytics.AdminAnalyticsService` as the read-only aggregation boundary.
- Added `/api/v1/admin/analytics/overview` for 7/30/90-day UTC operational analytics.
- Kept every monetary metric currency-separated; no cross-currency totals are emitted.
- Added gross/net order value, top-up/reversal flow, current wallet liability, order/fulfillment counts, dead letters, open financial cases, frozen wallets, reconciliation reviews, provider success/cost metrics, and daily activity.
- Added `/api/v1/admin/audit-logs` with tenant scoping, pagination, actor display, time/action/resource filters, and secret-key redaction defense.
- Added Admin UI Analytics and Audit views without adding third-party chart/runtime dependencies.
- Added query-path indexes via migration `7b8c9d0e1f23` for time-window analytics/audit workloads.
- Added repository documentation protocol: `CURRENT_STATE.md`, `AGENT_HANDOFF.md`, this log, and mandatory documentation-gate rules in `AGENTS.md`/repository skill.
- Added ADR-017 for analytics semantics and audit exposure boundaries.
- Verification: Phase 7.5 focused **3/3**; Phase 7.4 + Admin + 7.5 focused **17/17**; full runnable suite **169/169**; migration clean upgrade and `7b8c9d0e1f23 -> 5a6e7f8b9c10 -> 7b8c9d0e1f23` cycle passed; compileall/Admin+MiniApp JavaScript syntax/git diff check passed. Canonical Ruff and the two Aiogram-direct modules remain blocked by missing dependencies in this execution environment.

## Historical Milestones

Detailed implementation history for Phases 1–7.4 is retained in `AGENT_MAP.md` and `docs/prompts/prompt_history.md`. ADRs under `docs/decisions/` are authoritative for architectural rationale.

## 2026-09-15 — Phase 7.9.1–7.9.2 CI Gates & PostgreSQL Concurrency Verification

Current phase checkpoint: **Phase 7.9.2 — PostgreSQL Concurrency Verification**.

- Added GitHub Actions quality gates for Python 3.12 fast verification and a PostgreSQL 17 service-container concurrency job.
- Added canonical `Makefile` and `scripts/verify.sh` entry points so local/agent verification matches CI semantics.
- Added a repository handoff consistency check that validates current phase, roadmap/changelog coverage, migration-head existence, and handoff protocol markers.
- Added PostgreSQL-only integration tests for wallet overspend prevention, duplicate checkout exact-once behavior, duplicate settlement exact-once behavior, fulfillment claim single-winner semantics, and concurrent wallet creation.
- Hardened every `LedgerService` wallet balance mutation with tenant-scoped `SELECT ... FOR UPDATE` plus identity-map refresh before balance arithmetic.
- Added migration `8a9b0c1d2e34` to remove legacy payment primary-key indexes that caused Alembic drift, and removed duplicate ORM index declaration on `PaymentTransaction.provider_tx_id`.
- Added Dependabot coverage for Python and GitHub Actions dependencies.
- Added ADR-018 and `docs/architecture/verification-and-concurrency.md`.
- Local static gate in this build container: Python compileall, Admin/Mini App JavaScript syntax, and `git diff --check` passed. Full SQLite/PostgreSQL/Ruff execution is blocked here by missing database/development dependencies and must run in GitHub Actions or the target Ubuntu environment before milestone commit/push.

## 2026-09-15 — Phase 7.9.3–7.9.8 Production Readiness / Phase 8 Entry

Current phase checkpoint: **Phase 7.9.8 — Production Release Gate / Phase 8 Entry**.

- Replaced development-time `pip install -e .` service startup with one multi-stage immutable application image shared by migrations, API, worker, and bot runtime.
- Added non-root UID/GID, read-only root filesystem, tmpfs scratch space, no-new-privileges, healthchecks, graceful stop windows, and an explicit development override.
- Added structured JSON logging with defensive secret redaction, request correlation IDs, separate liveness/readiness, Redis service heartbeats, Prometheus-compatible API/queue/provider/financial-case metrics, and optional Prometheus alert configuration.
- Added production/staging fail-closed Settings validation, Redis-backed auth/money/Admin rate limiting, request-body limits, production API-doc disablement, security headers, trusted-proxy guidance, repository secret scanning, Gitleaks, and CI dependency audit.
- Added PostgreSQL backup, encrypted-production policy, destructive restore acknowledgement, isolated restore verification, retention handling, and DR documentation.
- Added staging-only seeded HTTP E2E that verifies bootstrap/catalog/checkout/idempotent replay/order visibility plus guarded failure injection for worker, Redis, and PostgreSQL recovery.
- Added deploy/rollback, observability/incident, security, migration/provider/financial incident, staging, backup/restore, and release-gate runbooks.
- Added `make release-gate` / `scripts/release_gate.sh`, producing `artifacts/release-evidence.json` tied to Git SHA and migration head after canonical verification + Compose validation + immutable Docker build. Staging/failure-injection evidence can be made mandatory for release candidates.
- Added ADR-019 and production-readiness architecture documentation.
- Static gates available in this environment passed. A dependency-limited fast regression on the final Phase 7.9.8 tree also passed **173 tests** with **7 PostgreSQL tests deselected**, excluding the two modules that directly import unavailable `aiogram`; the temporary `aiosqlite` shim used for this local run was external to the repository. Canonical Docker/PostgreSQL/Ruff/Aiogram/staging/restore execution remains explicitly blocked by missing runtime dependencies/services and must run externally before production cutover.

## 2026-09-15 — Phase 8.0 + 8.4 — Durable Bot Provisioning & Runtime Reconciliation

Current phase checkpoint: **Phase 8.0 + 8.4 — Durable Bot Provisioning & Runtime Reconciliation**.

- Added `BotProvisioningJob` as a durable tenant-scoped provisioning workflow with states, attempts, leases, retry scheduling, completion evidence, and `(tenant_id, idempotency_key)` uniqueness.
- Added provisioning request fingerprinting so an idempotency key cannot be replayed with different bot intent.
- Added runtime-only token resolution through `SecretStorage`; Admin APIs never return `token_secret_ref`, and provisioning/config payloads reject secret-like keys and Telegram-token-shaped values.
- Added Telegram `getMe` verification with `telegram_bot_id` as global identity authority, expected-username assertion, cross-tenant ownership rejection, and same-tenant convergence.
- Added worker bounded retry/backoff, stale-lease crash recovery, explicit terminal error codes, and AuditLog events for requested/ready/failed/retry/cancel lifecycle transitions.
- Added Admin Bot Factory inventory, durable job queue, retry/cancel, and enable/disable operations with STAFF read and ADMIN/OWNER mutation boundaries.
- Added bot-runtime desired-state reconciliation: newly enabled rows start, disabled/deleted rows stop, changed rows restart, and crashed polling tasks are recreated without process restart.
- Added provisioning/fleet operational metrics and starter Prometheus alerts.
- Added migration `9e1f2a3b4c56`, ADR-020, and `docs/architecture/bot-factory-provisioning.md`.
- Runnable verification: Phase 8 focused **9/9 passed**; fast regression **182/182 passed** excluding PostgreSQL and the two direct-Aiogram modules; SQLite clean upgrade/downgrade/re-upgrade plus `alembic check` passed; compileall/Admin JS/git diff checks passed. PostgreSQL/Ruff/Aiogram/Docker/staging canonical evidence remains externally pending.


## 2026-09-15 — Phase 8.1 + 8.5A Versioned Templates, Branding & Trial Wizard

Current phase checkpoint: **Phase 8.1 — Templates & Branding + 8.5A Wizard Core**.

- Added immutable code-defined bot templates with explicit key/version provenance and server-side validation for currency, locale, modules, colors, HTTPS branding URLs, support handles, and text lengths.
- Added template catalog API and server-side template resolution while retaining the legacy raw-config API path only for compatibility. The Admin normal path no longer asks users to author JSON.
- Added a guided Admin provisioning wizard for Template → Telegram secret reference/expected username → Branding/Modules → durable launch.
- Added audited post-provision bot configuration updates that preserve the existing credential reference and are ADMIN/OWNER-only.
- Added a signed `bot_id` JWT claim after verified Telegram Mini App authentication; storefront bootstrap uses that authoritative claim to apply per-bot public branding over tenant defaults.
- Added bot-specific Mini App name/accent/logo/tagline, Telegram `/start` store-button wording, and persistent menu text.
- Added privileged `/admin` Telegram Web App entry and `/whoami` diagnostics, plus `ADMIN_PUBLIC_URL`.
- Added `scripts/bootstrap_first_tenant.py` and `docs/runbooks/phase8-first-live-trial.md` so the first OWNER/control bot can be established without hand-editing the database.
- Added ADR-021 and `docs/architecture/bot-templates-branding.md`.
- No schema migration was required; migration head remains `9e1f2a3b4c56`.
- Verification: template/branding focused **3/3 passed**; full runnable regression **186/186 passed**, with **8 PostgreSQL tests deselected** and the two direct-Aiogram modules excluded in this dependency-limited container. Compile/JS/diff/security/handoff checks are run again before artifact packaging.

## 2026-09-15 — Phase 8.2A + 8.5B Easy Start & First-Run Web Installer

Current phase checkpoint: **Phase 8.2A — Dynamic Local Credential Vault + 8.5B First-Run Web Installer**.

- Added `python3 scripts/easy_start.py` as the recommended local first-run path. It generates missing development PostgreSQL/JWT/setup secrets, URL-encodes `DATABASE_URL`, starts the complete Docker stack, waits for readiness, and prints the setup URL.
- Added `/setup/` plus `/api/v1/setup/*`, protected by a random setup code and a database singleton installation lock.
- Added Telegram `getMe` verification to the web installer before first Tenant/OWNER/Bot persistence.
- Added a Fernet-encrypted shared local secret vault with atomic writes, file locking, restrictive permissions, environment-first compatibility, and dynamic visibility across API/worker/bot-runtime.
- Updated the Admin Bot Factory wizard to accept BotFather tokens directly; the API writes them to an opaque vault reference before the durable provisioning worker sees them. Direct-token idempotency is bound to a non-persisted SHA-256 credential fingerprint so reusing an idempotency key with different token material fails closed.
- Added per-tenant Mini App/Admin public URL settings so first-run URLs can be configured from the installer without process environment edits.
- Added migration `a1b2c3d4e5f6` for `system_install_state` and ADR-022 plus the Easy Start runbook.
- Verification: Easy Start focused **4/4 passed**; full runnable regression **191/191 passed** with **8 PostgreSQL tests deselected** and the two direct-Aiogram modules excluded. SQLite upgrade/downgrade/re-upgrade and `alembic check` passed.

## 2026-09-15 — Phase 8.2A.1 Easy Start Transport-Resilience Hotfix

Current phase checkpoint: **Phase 8.2A.1 — Easy Start transport resilience hotfix**.

- First Ubuntu live trial showed all Docker services starting successfully, but `scripts/easy_start.py` exited on a transient `ConnectionResetError` while polling `127.0.0.1:/health/ready` during Uvicorn startup.
- Hardened `wait_for_api()` so transient `OSError` transport failures (including connection reset/refusal) are retried until the existing readiness deadline instead of terminating the launcher immediately.
- Added a regression test that simulates one connection reset followed by a successful 200 readiness response.
- Verified the hotfix with Python compile plus an isolated direct transport-recovery test. No schema changes; migration head remains `a1b2c3d4e5f6`.

## 2026-09-15 — Phase 8.7 — Bot Factory Release Candidate

Current phase checkpoint: **Phase 8.7 — Bot Factory Release Candidate**.

- Live Ubuntu/Telegram Easy Start trial succeeded: the stack reached readiness and the control bot responded.
- Added bot credential lifecycle metadata (`credential_status`, `credential_version`, verified/rotated timestamps, normalized last error) while keeping token values only in SecretStorage.
- Added identity-safe credential verification/rotation; new BotFather tokens must resolve via Telegram `getMe` to the existing immutable `telegram_bot_id` before vault replacement.
- Added Redis-backed expiring fleet observation separated from PostgreSQL desired state, plus Admin fleet status, credential verify/rotate, and runtime restart operations.
- Added server-side tenant capacity controls for total bots, enabled bots, and open provisioning jobs.
- Added STABLE/CANARY release channels, runtime channel filtering, audited channel moves, and `docker-compose.rollout.yml` for candidate runtime ownership.
- Added server-authoritative launch readiness covering credential/runtime/HTTPS URLs/catalog/fulfillment/payment readiness.
- Added migration `b2c3d4e5f6a7`, ADR-023, fleet architecture/runbook documentation, and updated Easy Start/Phase 8 handoff state.
- Runnable verification: Phase 8 focused **24/24 passed**; full dependency-limited regression **198/198 passed**, excluding PostgreSQL and the two direct-Aiogram modules. SQLite upgrade through `b2c3d4e5f6a7` and `alembic check` passed. PostgreSQL/Ruff/direct-Aiogram/release-gate evidence remains external.
