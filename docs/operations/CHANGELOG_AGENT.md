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

## 2026-09-15 — Phase 9.0 — SaaS Plans & Entitlements Foundation

Current phase checkpoint: **Phase 9.0 — SaaS Plans & Entitlements Foundation**.

- Added platform-owned `SaaSPlan` records and exactly-one-per-tenant `TenantSubscription` state with provider-neutral external billing references and optional entitlement overrides.
- Added a fail-closed entitlement resolver. Subscribed tenants use validated plan limits; tenants with no subscription explicitly retain Phase 8 self-hosted `FACTORY_MAX_*` limits.
- Switched Bot Factory capacity enforcement for total bots, enabled bots, and open provisioning jobs to the resolved tenant entitlement snapshot.
- Added tenant usage measurement and `GET /api/v1/admin/saas/overview` for STAFF+ read-only plan, subscription, feature-flag, and usage visibility.
- Added an Admin **Plan** view while intentionally keeping plan assignment/mutation outside tenant authority for the upcoming platform billing/control plane.
- Added migration `c3d4e5f6a7b8`, ADR-024, `docs/architecture/saas-entitlements.md`, and Phase 9 entitlement/API enforcement tests.
- Verification in the dependency-limited workspace: Phase 9 focused **8/8 passed**; combined Phase 8 + Phase 9 focused **24/24 passed**; runnable regression **206/206 passed**, excluding PostgreSQL and the two direct-Aiogram modules. SQLite upgrade, `alembic check`, downgrade, and re-upgrade through `c3d4e5f6a7b8` passed. Canonical PostgreSQL/Ruff/Aiogram/Docker/staging/restore/release-gate evidence remains external.


## 2026-09-15 — Phase 9.1 — Platform Control Plane & Portable Hosting

### Implementation

1. Added installation-level platform authentication with `PLATFORM_ADMIN_TOKEN` / `X-GHBF-Platform-Token`, fully separate from tenant JWT/RBAC.
2. Added `/api/v1/platform/*` plan, tenant subscription, billing-event, overview, and global audit operations plus local `scripts/platformctl.py`.
3. Added `BillingEvent` provider/event-id idempotency with normalized-payload fingerprint conflict detection; raw provider payloads/secrets are not persisted.
4. Added `PlatformAuditLog` for global commercial mutations.
5. Changed plan retirement semantics so inactive plans reject new assignment while existing subscribers keep resolving until explicit migration/clear.
6. Added laptop-first portable export/import of PostgreSQL + encrypted local secret vault with quiesced mutation services, checksums, versioned manifest, GPG-first policy, safe extraction, and destination-owned `.env`.
7. Added migration `d4e5f6a7b8c9`, ADR-025/026, platform control-plane architecture, and laptop/VPS portability runbook.

### Verification

- Phase 9.0 + Phase 9.1 focused suites: **15/15 passed**.
- Runnable regression excluding PostgreSQL and the two direct-Aiogram modules: **213/213 passed**, with 8 PostgreSQL tests deselected.
- SQLite upgrade -> `alembic check` -> downgrade -> upgrade through `d4e5f6a7b8c9`: passed.
- Python compileall and portable shell syntax checks: passed.
- Ruff, real PostgreSQL concurrency, direct-Aiogram, Docker portable export/import drill, staging recovery, and full release gate remain canonical external evidence.

## 2026-09-15 — Phase 9.2 — Billing Provider Adapter & Customer Portal

1. Added provider-neutral billing adapter interfaces with Stripe as the first signed implementation; provider credentials remain environment-only.
2. Added operator-owned `SaaSPlanPrice` records so external provider price IDs and commercial intervals stay separate from entitlement definitions.
3. Added tenant OWNER/ADMIN checkout and hosted customer-portal handoff without allowing browser redirects to mutate authoritative subscription state.
4. Added Stripe webhook signature verification and normalized convergence through the Phase 9.1 `BillingEvent` idempotency/fingerprint boundary.
5. Added laptop-first pull reconciliation via `platformctl billing-reconcile`, allowing provider state convergence without permanent public ingress or a webhook tunnel.
6. Added deterministic persisted past-due grace deadlines while keeping commercial enforcement observe-only until Phase 9.3 server-side product gates.
7. Fixed billing-event replay fingerprinting by canonicalizing datetimes/UUIDs/enums before hashing, so equivalent provider events remain idempotent across DB round-trips.
8. Added migration `e5f6a7b8c9d0`, ADR-027, billing architecture documentation, and a local-first SaaS billing runbook.

### Verification

- Phase 9.0 + 9.1 + 9.2 focused suites: **20/20 passed**.
- Fast runnable regression excluding PostgreSQL and the two direct-Aiogram modules: **219/219 passed** (8 PostgreSQL tests deselected).
- SQLite upgrade -> `alembic check` -> downgrade -> upgrade through `e5f6a7b8c9d0`: passed with no drift.
- Python compile and Admin JavaScript syntax checks passed during implementation; final packaging gate reruns them.
- Canonical PostgreSQL, Ruff, direct-Aiogram, immutable Docker, staging recovery, portable restore drill, and full release-gate evidence remain external requirements.

## 2026-09-16 — Phase 9.3 — Product Entitlements

Current phase checkpoint: **Phase 9.3 — Product Entitlements**.

- Added centralized commercial access states (`SELF_HOSTED`, `ACTIVE`, `GRACE`, `BLOCKED`) derived from normalized subscription state and grace deadline.
- Added server-side commercial feature gates for custom branding, canary rollout, and runtime controls plus growth gates for provisioning/retry/enablement.
- Preserved self-hosted tenants with no subscription as fully functional and kept safe contraction/security operations available when commercial access is blocked.
- Updated Tenant Admin to display effective versus configured entitlements without making UI state authoritative.
- Added ADR-028 and focused Phase 9.3 coverage. No schema migration required.

## 2026-09-16 — Phase 9.4 — SaaS Operations

Current phase checkpoint: **Phase 9.4 — SaaS Operations**.

- Added periodic billing pull reconciliation inside the worker so laptop/self-hosted installations converge without permanent public webhook ingress.
- Added provider synchronization timestamp/source/error evidence to `TenantSubscription`.
- Added SaaS health aggregation and alerts for grace, blocked commercial access, provider errors, and stale synchronization.
- Added platform tenant-entitlement inspection without tenant JWT issuance or impersonation; inspection is audited in `PlatformAuditLog`.
- Added `platformctl saas-health` and `platformctl tenant-entitlements` for localhost/SSH operations.
- Added migration `f6a7b8c9d0e1` and ADR-029.

## 2026-09-16 — Phase 10.0 — Provider Platform Core

Current phase checkpoint: **Phase 10.0 — Provider Platform Core**.

- Promoted the legacy supplier engine into a capability-driven Provider Platform while preserving existing provider rows, product mappings, routing, reconciliation, and failover behavior.
- Added canonical provider categories: `NUMBER`, `ACCOUNT`, `GIFT`, `DIGITAL_PRODUCT`, `SERVICE`, and `OTHER`.
- Added safe adapter manifests declaring supported categories, capabilities, credential requirements, driver family, and operator-facing metadata.
- Added Admin adapter discovery and category-compatible provider creation; vendor names remain outside commerce/business logic.
- Added write-only provider credential entry into `SecretStorage`. The default self-hosted path stores values in the encrypted local vault while PostgreSQL stores only deterministic secret references; environment references remain supported.
- Added durable provider health evidence and bounded connection/order timeouts. Connection-test messages are bounded and credential values are redacted before persistence/response.
- Updated the Admin provider workflow to choose adapter/category and either encrypted credential value or environment reference.
- Added migration `a7b8c9d0e1f2`, ADR-030, and `docs/architecture/provider-platform.md`.

### Verification

- Phase 9.4 + provider compatibility focused gate: **23/23 passed**.
- Phase 10.0 provider-core suite: **5/5 passed**.
- Dependency-limited runnable regression: **232/232 passed**, excluding the PostgreSQL-only suite and two direct-Aiogram modules.
- SQLite upgrade -> `alembic check` -> downgrade -> upgrade through `a7b8c9d0e1f2`: passed with no drift.
- Canonical PostgreSQL/Ruff/direct-Aiogram/Docker/staging/restore/release-gate evidence remains external.
- Final packaging-tree compileall, JavaScript syntax, handoff consistency, and temporary-Git secret scan passed. Phase 10.0 -> 10.5 staged patch `git diff --check` also passed. Ruff is unavailable in this execution environment; host `pip check` reports an unrelated pre-existing `moviepy`/`Pillow` conflict.

## 2026-09-16 — Phase 10.1–10.5 — Provider & Integration Platform Completion

Current phase checkpoint: **Phase 10.5 — Provider Order Lifecycle (Phase 10 complete)**.

- Added vendor-neutral canonical provider order states/delivery artifacts and category-specific operations, including deterministic number/SMS sandbox lifecycle.
- Added constrained Generic HTTP/OpenAPI integration mapping with dynamic capabilities/credentials, bounded HTTP execution, redirect refusal, DNS/IP SSRF protection, production HTTPS/host allowlisting, and recursive credential redaction.
- Added tenant-scoped routing policies for priority, lowest cost, availability, health, deterministic weighted selection, and manual provider preference.
- Restricted automatic cross-provider failover to explicitly pre-order-safe failures; timeout/network ambiguity enters reconciliation instead of risking duplicate upstream purchases.
- Added normalized provider-offer observations with freshness, stock/availability, quantity limits, and currency-safe cost routing.
- Integrated non-terminal canonical provider states into durable fulfillment; only `COMPLETED` fulfills, while polling reconciliation converges active orders without requiring public webhooks.
- Multi-item async attempts that would need more than one upstream correlation fail safe to `UNKNOWN` pending future per-item saga support.
- Added migrations `b8c9d0e1f2a3` and `c9d0e1f2a3b4`; added ADR-031/032/033 and expanded provider-platform architecture documentation.

### Verification

- Dependency-limited runnable regression: **253/253 passed**, excluding PostgreSQL-only tests and the two direct-Aiogram modules unavailable in this execution environment.
- SQLite upgrade through `c9d0e1f2a3b4`, `alembic check`, downgrade, and re-upgrade: passed.
- Canonical PostgreSQL/Ruff/direct-Aiogram/Docker/staging/restore/release-gate evidence remains external.
- Final packaging-tree compileall, JavaScript syntax, handoff consistency, and temporary-Git secret scan passed. Phase 10.0 -> 10.5 staged patch `git diff --check` also passed. Ruff is unavailable in this execution environment; host `pip check` reports an unrelated pre-existing `moviepy`/`Pillow` conflict.

## 2026-09-16 — Phase 11.0–11.5 — Payments Platform Completion

Current phase checkpoint: **Phase 11.5 — Payments Operations & Risk Hardening (Phase 11 complete)**.

- Added payment-method and immutable payment-observation domain records plus assurance metadata over the existing exactly-once ledger settlement gate.
- Added manual approval and installation-owned on-chain verification for TRON USDt and generic EVM tokens; transaction identity is normalized before replay uniqueness checks.
- Added NOWPayments, Triple-A, Bybit Pay, Binance Pay, and GoZaPay adapters with provider-specific authentication/status semantics and pull reconciliation.
- Bybit supports signed callbacks and merchant-reference creation recovery; Binance and Triple-A unsupported callback/refund paths remain fail-closed.
- Added generic provider-payment reconciliation worker so laptop/self-hosted installs converge without permanent public ingress.
- Added payment operations health and Financial Center visibility for stale/unknown payments, creation ambiguity, manual reviews, reversal reconciliation, and financial cases.
- Added GoZaPay as an explicitly experimental adapter. GHBF waits for `settled` before wallet credit and requires explicit operator risk/parity acknowledgement. Flexible/open-amount invoices are not auto-credited to fiat wallets until a later multi-asset/FX contract exists.
- Added migration `d0e1f2a3b4c5`, ADR-034/035, and the payment-platform architecture guide.

### Verification

- Payment/wallet/provider regression: **123/123 passed**.
- Dependency-limited runnable regression: **344/344 passed**, excluding only the two direct-Aiogram modules because `aiogram` is unavailable in this execution environment.
- Fresh SQLite upgrade -> `alembic check` -> downgrade to `c9d0e1f2a3b4` -> re-upgrade to `d0e1f2a3b4c5` -> second `alembic check`: passed.
- Canonical real-PostgreSQL/Ruff/direct-Aiogram/Docker/staging/restore/release-gate evidence remains external before production cutover.


## 2026-09-16 — Phase 12.0–12.3 — Commerce Economics / Reseller Engine Completion

Current phase checkpoint: **Phase 12.3 — Commerce Economics Operations (Phase 12 complete)**.

- Added high-precision asset wallets and immutable idempotent asset-ledger entries.
- Added tenant-owned PARITY/FIXED_RATE FX policies and idempotent wallet holds.
- Added GoZaPay flexible/open-amount deposit sessions with optional per-payment-method auto-credit. Asset auto-credit is exact; fiat auto-credit requires an explicit matching FX policy and otherwise remains review-only.
- Added reseller/VIP pricing tiers plus global/category/product/variant fixed/percentage/mixed markup rules, minimum margins, rounding controls, and immutable checkout price quotes.
- Added actual supplier-cost and gross-profit attribution after fulfillment plus provider/bot/order-item economics.
- Added provider-balance polling, durable low-balance/error evidence, and Admin economics visibility without automatic routing/provider mutation.
- Corrected legacy provider-mapping fixtures and fulfillment semantics so `product_id` is always canonical `Product.id` and `product_variant_id` carries the optional variant refinement.
- Hardened direct provider balance refresh against async lazy-loading by explicitly eager-loading credentials.
- Added migration `e1f2a3b4c5d6`, ADR-036, and `docs/architecture/commerce-economics.md`.

### Verification

- Phase 12 focused provider/commerce/economics regression: **33/33 passed**.
- Dependency-limited runnable regression: **355/355 passed**, excluding only the two direct-Aiogram modules because `aiogram` is unavailable in this execution environment.
- Fresh SQLite upgrade -> `alembic check` -> downgrade to `d0e1f2a3b4c5` -> re-upgrade to `e1f2a3b4c5d6` -> second `alembic check`: passed.
- Canonical real-PostgreSQL/Ruff/direct-Aiogram/Docker/staging/restore/release-gate evidence remains external before production cutover.


## Milestone 41: Phase 13 — Advanced Bot Factory & Operator Experience

- **Date:** 2026-09-16
- **Status:** Core feature roadmap implemented; external release qualification pending

1. Added advanced vendor-neutral bot templates for multi-API reseller, number/SMS, accounts, gifts, digital products, and hybrid stores.
2. Added a five-stage Admin wizard with live tenant provider/payment/pricing choices and server-side ownership/category validation.
3. Added authoritative per-bot business profiles in the versioned Bot config/provisioning snapshot and enforced them in storefront payments, pricing, flexible auto-credit, and fulfillment routing.
4. Preserved legacy product routing when no business profile exists and made malformed persisted profiles fail closed.
5. Added `scripts/doctor.py`, daily Make targets, the end-to-end operator guide, final release-qualification checklist, ADR-037, and advanced Bot Factory architecture documentation.
6. No schema change was required; migration head remains `e1f2a3b4c5d6`.
7. Dependency-limited runnable regression: **361/361 passed** excluding only the two direct-Aiogram modules unavailable in this environment. Canonical PostgreSQL/Ruff/Docker/staging/restore evidence remains required before production cutover.

## 2026-09-16 — Phase 13 Repair Review

- Added `REPAIR_REVIEW_2026-09-16.md` with prioritized payment-policy, reversal, import-recovery, profile-validation, and Mini App integration findings and acceptance criteria.
- Corrected CURRENT_STATE's stale recommendation to start Phase 13 and AGENT_MAP's Phase 12 header; recorded the review in prompt history and roadmap.
- Verification: 371 non-PostgreSQL tests passed, 8 deselected; canonical verification fails at Ruff (197 findings). Local pip module is missing; PostgreSQL test URL is unconfigured. Compileall, client JavaScript syntax, handoff consistency, and tracked whitespace checks passed.
- No application changes or migration. Head remains `e1f2a3b4c5d6`. Repairs and release qualification remain pending; no commit/push.

## 2026-09-16 — Repair baseline — Admin access & financial hardening

- Added browser Admin sign-in using private Telegram-issued, Redis-backed, single-use codes; preserved live tenant RBAC and token revocation checks.
- Repaired bot payment policy bypass, strict auto-credit validation, customer payment-method/deposit integration, credited-deposit reversal monitoring, and failed-import restart behavior.
- Added PostgreSQL JSONB alignment migration `f2a3b4c5d6e7`; fixed concurrent immutable quote insertion using savepoint/re-read.
- Restored lint and dependency verification; added browser, auth, import-failure, and financial concurrency regressions. Final results live in the repair patch notes.
- New Phase 13 development remains on hold by explicit user request. Existing imported code is preserved. No production cutover claimed.
- Resumed after the interrupted session (usage-limit stop): created the missing [repair patch notes](REPAIR_PATCH_NOTES_2026-09-16.md) and [ADR-038](../decisions/ADR-038-repair-auth-and-financial-boundaries.md) that CURRENT_STATE/AGENT_MAP/roadmap already referenced.
- Canonical gate completed green on this checkout with the disposable PostgreSQL test container (`ghbf_repair_test` on 127.0.0.1:55439): Ruff clean, **396 fast tests passed** (13 deselected), **13 PostgreSQL tests passed** including the five new economics concurrency races, alembic upgrade/check clean at head `f2a3b4c5d6e7`, handoff/secret/JS/compile/`pip check` gates passed. Bootstrapped the venv's missing `pip` module via `ensurepip` so the canonical `pip check` step runs.
- Docker release-gate, staging failure injection, restore drill, and live-provider evidence remain unclaimed; the Compose application still serves the previous immutable image on port 8010. At that checkpoint no commit/push had been authorized.

## 2026-09-17 — Admin testing and version-control handoff

- User authorized commit/push of the imported baseline and repairs; preserved existing host containers under Law 4.
- Reran canonical verification: 396 fast and 13 PostgreSQL tests passed, PostgreSQL migration drift clean. Focused Admin authentication (10 tests) and mocked Admin/Mini App browser checks passed.
- Corrected repair notes to identify the actual funding gate, reversal service, quote service, and failed-import limitations. Supplied operator-run rebuild/migration/restart and private Telegram Admin sign-in instructions; no deployment or production-readiness claim.

## 2026-09-17 — Phase 13 — Advanced Bot Factory UX & Storefront Vertical Delivery

- Delivered storefront vertical adaptation in Telegram Mini App (`apps/miniapp/static/app.js`, `index.html`, `styles.css`) adapting dynamically to bot template and business profile (`NUMBER_SMS`, `ACCOUNT_SHOP`, `GIFT_CARDS`, `DIGITAL_PRODUCTS`, `MULTI_RESELLER`).
- Dynamic navigation filtering based on bot `enabled_modules` (`catalog`, `orders`, etc.).
- Exposed provider fulfillment delivery artifacts (`kind`, `value`, `fields`) in `FulfillmentSummary` via `apps/api/v1/storefront.py` (`/orders` and `/orders/{order_id}`) for the authenticated order owner.
- Added interactive copy-to-clipboard functionality with haptic and toast feedback for delivered codes, vouchers, keys, credentials, and virtual numbers.
- Enhanced Admin console fleet list (`apps/admin/static/app.js`, `styles.css`) with vertical business profile badges, provider counts, and routing strategies.
- Updated `docker-compose.yml` and `.env.example` to bind `API_BIND_ADDRESS` (default `0.0.0.0`) on port 8010 for accessible LAN testing while strictly preserving port 8000 for Portainer (Law 4).
- Verification: 399 fast tests passed; 13 PostgreSQL tests passed; Ruff clean; JavaScript syntax clean; handoff consistency clean.

## 2026-09-17 — Admin Persistent Session & Ready URL Sign-In

- Added `localStorage` session persistence (`ghbf_admin_token`) in `apps/admin/static/app.js` so browser reload preserves the active admin session instead of logging out.
- Added direct URL sign-in: Telegram bot `/admin` command now formats a ready clickable link (`f"{base_admin}/?code={code}"`), and frontend automatically captures `code` from query/hash params, signs in, and cleans the URL via `history.replaceState`.
- Added explicit Exit buttons in both header (`#headerSignOut`) and sidebar (`#signOut`) that clear stored session credentials.
- Configured default `ADMIN_PUBLIC_URL` LAN binding (`http://10.70.5.5:8010/admin/`).
- Added test coverage in `tests/test_admin_browser_login.py` (private chat direct URL generation and public URL handling).

## 2026-09-17 — Admin Browser Cache Elimination & Container Rebuild

- Eliminated browser script caching for static admin web app: added `Cache-Control: no-cache, no-store, must-revalidate` and `Pragma: no-cache` in `SecurityHeadersMiddleware` for `/admin`, `/miniapp`, and `/setup`.
- Added query string cache-busting `v=20260917_03` to script and stylesheet tags in `apps/admin/static/index.html`.
- Bound inline `onclick="handleSignOut(event)"` to Exit buttons in header and sidebar as a fail-safe trigger.
- Exposed `window.handleSignOut` globally in `apps/admin/static/app.js` with instant UI transition to `showLogin()`.
- Verification: 401 fast tests passed; 13 PostgreSQL concurrency tests passed; 12 admin browser login tests passed; ruff clean; git diff check clean.

## 2026-09-17 — Admin Mobile Responsiveness Optimization

- Transformed desktop sidebar navigation into a touch-scrollable horizontal pill bar with active accent states on mobile screens (`max-width: 820px`).
- Streamlined mobile brand header and hid desktop sidebar foot.
- Stacked search/filter toolbars, provider grids, analytics currency cards, and dialog action grids into full-width responsive controls.
- Enforced 16px minimum font size on mobile inputs and selects to eliminate iOS Safari automatic viewport zooming.
- Added 2-column auto-scaling layout for metrics with text wrap protection.
- Bounded modals/dialogs to `calc(100vw - 20px)` with touch-friendly wizard step indicator scrolling.
- Bumped cache-busting query strings to `v=20260917_04` in `apps/admin/static/index.html`.
- Verification: 401 fast tests passed; 13 PostgreSQL concurrency tests passed; 12 admin browser login tests passed; ruff clean; git diff check clean.

## 2026-09-17 — Mobile Navigation Grid & Header Unsticking

- Replaced single-row horizontal pill navigation with a structured 3-column responsive grid on mobile (`max-width: 820px`), ensuring all 12 page buttons (Overview, Plan, Bots, Analytics, Products, Orders, Fulfillment, Providers, Members, Financial Center, Audit, Evidence) are immediately visible and pressable.
- Switched mobile `.sidebar` from `position: sticky` to `position: static` so the header is never stuck blocking page content and scrolls naturally.
- Converted `.shell` to flex-column container so content is never pushed off to the right.
- Bumped cache-busting query strings to `v=20260917_05` in `apps/admin/static/index.html`.
- Rebuilt Docker image `gh-bot-factory:local` and recreated API, Worker, and Bot Runtime services.
- Verification: 401 fast tests passed; 13 PostgreSQL concurrency tests passed; 12 admin browser login tests passed; ruff clean; git diff check clean.

## 2026-09-17 — Local Installation Security Hardening

- Milestone implementation commit: `76ce536394f7b4b2088870d53f5bb153551ab390`.
- Created a restricted PostgreSQL dump and proved it by restoring into an isolated temporary database at migration head `f2a3b4c5d6e7` with tenant data readable.
- Rotated the Compose PostgreSQL role and matching ignored `.env` credentials atomically without printing or committing the secret; recreated this project's PostgreSQL/API/worker/bot-runtime services and confirmed them healthy.
- Enabled Redis-backed request rate limiting and narrowed the host API bind from all interfaces to `10.70.5.5:8010`.
- Repaired backup/restore scripts so `.env` is parsed as data rather than sourced as shell code and checksum files remain valid when verified from their containing directory.
- Updated the live doctor to probe the configured API bind address instead of assuming loopback; `make doctor-live` now passes with all five services, readiness, and Alembic head verified.
- Added a split-horizon local HTTPS runbook for `botfac.gh-store.me` using the existing Nginx Proxy Manager plus UDM local DNS. This dedicated name avoids the existing `bot.gh-store.me` Zero Trust route. The application remains in development mode with the existing LAN Admin URL until the operator creates the DNS/TLS proxy entries; no Cloudflare tunnel or unrelated host container was changed.
- Canonical verification: Ruff/static/secret/handoff/JavaScript/dependency checks passed, 401 fast tests passed, 13 PostgreSQL tests passed, and Alembic reported no drift at `f2a3b4c5d6e7`.
- Follow-up hostname correction: commit `c1a1d207181cc6aef99c01fed306dd78c6e4acdb` changed the planned endpoint to `botfac.gh-store.me` because `bot.gh-store.me` already belongs to the existing Zero Trust route.

## 2026-09-17 — Local `botfac.gh-store.me` Activation

- Network-activation handoff commit: `11f3257cb08315ec2f51c700f4e2b6f78eb00b0c`.
- With explicit operator authorization, added an idempotent UDM static A record for `botfac.gh-store.me -> 10.70.5.5`; preserved a pre-change static-DNS backup under `/data/ghbf-backups/` and left the existing `bot.gh-store.me` route untouched.
- Created Nginx Proxy Manager proxy host ID 41 through its internal validated creation path after taking a consistent root-only SQLite backup. The host forwards HTTP/WebSockets to `10.70.5.5:8010`, blocks common exploits, and has a valid generated Nginx configuration.
- Activated `ADMIN_PUBLIC_URL=http://botfac.gh-store.me/admin/` for the local development environment and recreated API/bot-runtime services.
- Verified UDM DNS, Nginx syntax, proxied readiness with PostgreSQL/Redis healthy, Admin HTTP 200, and all five GH-Bot-Factory services healthy.
- Trusted TLS is not yet available for `gh-store.me` in Nginx Proxy Manager. `APP_ENV=staging` and `MINIAPP_PUBLIC_URL` remain intentionally unset until a trusted certificate is attached; no Cloudflare tunnel was changed.

## 2026-09-17 — Phase 14 Customer Marketplace Product Definition

Current phase checkpoint: **Phase 14 — Customer Marketplace product definition**.

- Recorded the Factory Owner's intended business model for selling Telegram bots and Mini Apps, allowing customers to operate their own tenant catalogs/providers, pricing API integrations, and offering premium self-hosted/source delivery.
- Added `docs/plans/phase-14-customer-marketplace.md` with actor boundaries, explanations for all eleven current templates, the public configurator journey, manual inquiry/quote workflow, pricing composition, integration catalog, tenant onboarding, and deployment/source handoff proposal.
- Recommended a strict split between platform authority, tenant ownership, and shopper identity, with no owner impersonation and no implicit control after an external handoff.
- Defined six proposed implementation slices from template guidance through release qualification. This is documentation and discussion only; no Phase 14 application code, schema, route, or deployment change was made.
- Planning commit: `9d2e8585312159363390f9df6433e18600ecf2c8`.

## 2026-09-18 — Phase 14.0 — Template Guidance & Marketplace Boundaries

- Milestone implementation: Phase 14.0 delivered.
- Added ADR-039 (`docs/decisions/ADR-039-phase14-marketplace-boundaries-and-template-guidance.md`) defining the 4-tier authority boundaries (Factory Owner, Customer/Tenant Owner, Customer Staff, Shopper, Self-Hosted/Source Licensee) and commercial pricing layers.
- Implemented server-authoritative `TemplateGuidance` in `packages/factory/templates.py` covering all eleven templates with `product_source` (`stored`, `provider_api`, `hybrid`), `what_you_can_sell`, `delivery_experience`, `operational_complexity` (`Low`, `Medium`, `High`), `setup_requirements`, `example_business`, `limitations`, and `supported_hosting`.
- Exposed `guidance` in `BotTemplateResponse` on `GET /api/v1/admin/bots/templates` for internal Admin and public configurator consumption.
- Added interactive `? Help & Guidance` drawer in Admin Bot Creation Wizard with live keyword search, categorization filter chips (`All`, `Stored`, `Live APIs`, `Hybrid`), complexity badges, and 1-click template selection.
- Enriched the inline template preview in the wizard to show product source, complexity badges, and sellable goods highlights.
- Added `tests/test_phase14_template_guidance.py` covering all eleven template schemas and API integration.
- Bumped Admin asset cache busters to `v=20260918_01` in `apps/admin/static/index.html`.
- Rebuilt Docker image `gh-bot-factory:local` and verified live on Compose services (`api`, `worker`, `bot-runtime`).
- Canonical verification: Ruff clean, 403 fast tests passed, 13 PostgreSQL concurrency tests passed, Alembic no-drift clean at `f2a3b4c5d6e7`.
