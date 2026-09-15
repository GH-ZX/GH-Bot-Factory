# User Prompt & Milestone History Ledger

This document serves as the historical record of all user prompts, architectural directives, code review findings, and delivered milestones for **GH-Bot-Factory**.

> [!IMPORTANT]
> **MANDATORY INSTRUCTION FOR ALL CODING AGENTS:**
> Whenever the user provides a new milestone prompt or request, you **MUST** record the prompt verbatim in this ledger, document the date/time, and summarize the files changed, migrations created, test outcomes, and Git commit SHAs.

---

## Milestone 1: Foundation & Repository Initialization (Phase 1)

- **Date:** 2026-09-15
- **Status:** Completed
- **Commit SHA:** `913a48e` (Foundation baseline)

### User Request / Prompt:
> Initialize the GH-Bot-Factory project with FastAPI, uv virtualenv (Python 3.12), directory structure, health check endpoint, and clean git configuration. Work directly in `/opt/gh-bot-factory` (linked to `/home/it/Coding/gh-bot-factory`). Ensure port 8000 is untouched (Portainer).

### Deliverables & Implementation:
- FastAPI application scaffolding in `apps/api/main.py`.
- Health check endpoint `/health`.
- `pyproject.toml` dependencies with uv package management.
- Initialized Git repository and connected to remote `git@github.com:GH-ZX/GH-Bot-Factory.git`.

---

## Milestone 2: Multi-Tenancy + Database Core + Ledger (Phase 2)

- **Date:** 2026-09-15
- **Status:** Completed
- **Commit SHA:** `714e815`

### User Request / Prompt:
> "Proceed with Phase 2 of GH-Bot-Factory. The Foundation is complete and tested. Implement Multi-Tenancy + Database Core + Authentication/Authorization + Commerce Domain + Order State Machine + Ledger + Provider abstraction according to the architecture above. Work directly in /opt/gh-bot-factory, run migrations/tests/linting, commit each coherent milestone to GitHub, and never commit secrets. Do not modify existing Docker containers or Cloudflare Tunnel configuration. Port 8000 is occupied by Portainer; do not use it. Before coding, inspect the current repository and preserve the existing Foundation. At the end, provide a concise implementation report with files changed, migrations, tests, commit SHAs, and any blockers."
> 
> *Directive: "answare in english, no answares in arabic"*

### Deliverables & Implementation:
- **Tenants & RBAC:** `Tenant`, `User`, `Membership` models, role-based access control.
- **Commerce Domain:** `Category`, `Product`, `ProductVariant`, `Order`, `OrderItem`.
- **State Machine:** Strict legal transitions enforced by `OrderStateMachine` (`OrderStatus`).
- **Ledger:** Double-entry accounting via `Wallet`, `LedgerTransaction`, and `LedgerService` with mathematical balance reconstruction.
- **Provider Protocol:** Base `Provider` interface and initial mock provider.
- **Verification:** 11/11 tests passing, clean `ruff`, committed to `main`.

---

## Milestone 3: Multi-Tenant Telegram Runtime (Phase 3)

- **Date:** 2026-09-15
- **Status:** Completed
- **Commit SHA:** `8520386`

### User Request / Prompt:
> "Proceed with Phase 3 — Telegram Runtime for GH-Bot-Factory.
> The current Core milestone is complete: Multi-tenancy + RBAC verified, Commerce + strict order state machine verified, Double-entry ledger verified, Provider abstraction + mock provider operational, 11/11 tests passing, Ruff clean, Git clean and synced.
> Do NOT rewrite or break the existing Core.
> Implement the Telegram Runtime as a reusable multi-tenant runtime using Aiogram 3.
> Requirements:
> 1. Bot Registry (associated with Tenant, token masked with secret ref, enabled/disabled state, unique Telegram bot identity).
> 2. Secret Storage Protocol (EnvSecretStorage).
> 3. Middleware Pipeline (Correlation, TenantResolution, UpdateLogging, ErrorHandling).
> 4. Tenant-scoped customer bindings (TenantTelegramUser).
> 5. Modular Feature Routers (/start, /menu, /catalog, /orders, /account).
> 6. Isolated FSM storage per (tenant_id, bot_id, user_id)."

### Deliverables & Implementation:
- `Bot` model, `TenantTelegramUser` model, Alembic migration `ed2d7cf07aaf`.
- `BotRuntimeManager` for concurrent multi-bot polling/lifecycle management.
- `SecretStorage` protocol and `EnvSecretStorage` resolver.
- Middleware stack: `CorrelationMiddleware`, `TenantResolutionMiddleware`, `UpdateLoggingMiddleware`, `ErrorHandlingMiddleware`.
- Feature routers and isolated `TenantFSMHelper`.
- ADR-003 and architecture documentation.
- 25/25 tests passing.

---

## Milestone 4: Provider Engine, Routing & Fulfillment (Phase 4)

- **Date:** 2026-09-15
- **Status:** Completed
- **Commit SHAs:** `5eae8c5`, `d9a8baa`, `5c992a2`, `15a4882`

### User Request / Prompt:
> "Proceed with Phase 4 — Provider Engine, Routing, and Fulfillment for GH-Bot-Factory.
> Build a production-grade Provider Engine and Fulfillment Service allowing tenants to sell products through one or more external providers without coupling Commerce Core or Telegram handlers.
> Requirements:
> 1. Provider models, credentials with secret refs, product mappings with priority and cost.
> 2. Provider protocol (health, balance, products, orders), BaseProviderClient, MockProvider, ExampleDigitalCodesProvider, ProviderClientRegistry.
> 3. Resilient ProviderRouter with priority routing and failover.
> 4. FulfillmentService with idempotency keys, background FulfillmentWorker with backoff, CheckoutService with DB price validation, and ReconciliationService.
> 5. Architecture documentation and ADRs."

### Deliverables & Implementation:
- Models: `Provider`, `ProviderCredential`, `ProviderProductMapping`, `FulfillmentAttempt`. Alembic migration `f62520a5ff1f`.
- Client Registry, Mock Provider with failure simulations, Digital Codes Provider, Provider Router.
- Fulfillment Service, Worker Queue with exponential backoff, Checkout Service, Reconciliation Service, Notification Service.
- ADR-004, ADR-005, and architecture guides.
- 38/38 tests passing.

---

## Milestone 5: Production Hardening (Phase 4.1)

- **Date:** 2026-09-15
- **Status:** Completed
- **Commit SHA:** `bcee3a1`

### User Request / Prompt:
> *External Code Review:*
> "1. Critical — Retryable failure currently treated as failed and sends false refund message to user.
> 2. Critical — 'At-Most-Once' claim is not guaranteed if app crashes after provider acceptance before saving external_order_id.
> 3. Critical — Worker relies only on in-memory asyncio.Queue; jobs vanish on server crash.
> 4. High — Reconciliation does not execute automated ledger refund when provider confirms failure.
> 5. High — Fulfillment assumes single item (order.items[0]); need multi-item support.
> Action: Implement Phase 4.1 — Production Hardening:
> - Proper RETRYING / UNKNOWN states.
> - Do not send refund notifications unless refund actually executed.
> - Strict refund idempotency.
> - Reconciliation executes automated refund upon confirmed upstream failure.
> - Durable fulfillment jobs in DB (FulfillmentJobRecord) with startup crash recovery.
> - Tests for crash recovery, timeout after acceptance, duplicate execution suppression, and multi-item orders.
> - Never leak raw str(exc) to users."

### Deliverables & Implementation:
- Added `UNKNOWN` status to `FulfillmentStatus`.
- Added `FulfillmentJobRecord` model (`fulfillment_jobs` table) with states `QUEUED`, `RUNNING`, `COMPLETED`, `FAILED`, `DEAD_LETTER`.
- Added `order_item_id` to `FulfillmentAttempt`. Alembic migration `8bfc9b56eb94`.
- Enforced strict refund idempotency in `LedgerService.refund()` on `(wallet_id, reference_id, reference_type)`.
- Implemented `FulfillmentWorker.recover_pending_jobs(session)` to recover abandoned jobs on startup.
- Implemented idempotency-key lookup fallback in `ReconciliationService` for orders abandoned mid-flight.
- Added multi-item order fulfillment handling in `FulfillmentService`.
- Replaced premature refund notifications with progress updates; eliminated raw exception leaks.
- Added comprehensive test suite in `tests/test_phase4_hardening.py` (7 new tests).
- Updated ADR-005 and `docs/architecture/fulfillment.md`.
- 45/45 tests passing.

---

## Milestone 6: Coding Agent Map & Project Knowledge Base

- **Date:** 2026-09-15
- **Status:** Completed
- **Commit:** `aa0f7ab`

### User Request / Prompt:
> "add a coding agent map file md , a skill so coding agents knows everything and what happened so easly access everything, inside it all the laws of this project, and inside this skill, how things done, and my prompts, every update should be typed there or in a file so skill redirect to it so this file is mapping all, including my prompts"

### Deliverables & Implementation:
- Created `AGENT_MAP.md` at repository root.
- Created Antigravity Skill `.agents/skills/gh-bot-factory-core/SKILL.md`.
- Created `docs/prompts/prompt_history.md` (this ledger).
- Created `AGENTS.md` and `GEMINI.md` root rules to activate agent guidelines automatically.

---

## Milestone 7: Phase 4.2 — Final Fulfillment Integrity Hardening

- **Date:** 2026-09-15
- **Status:** Completed
- **Commit:** `6d750b7`

### User Request / Code Review Findings:
> Code review of main identified 3 critical financial & concurrency issues before Phase 5:
> 1. Canonical Refund Idempotency: FulfillmentService and ReconciliationService used mismatched reference_type ("FULFILLMENT_FAILURE_REFUND" vs "RECONCILIATION_FAILURE_REFUND"), risking duplicate credits.
> 2. Durable Enqueue Fail-Closed: When persist_db=True, a database insert error logged a warning but still placed the job in the in-memory queue, risking memory-only stranded jobs.
> 3. Atomic Worker Job Claim: Worker claimed jobs with non-atomic select-then-update, vulnerable to race conditions under concurrent workers.
> 4. Refund Amount Integrity: Ensure refund amount strictly uses order.total_amount, never recalculating against mutable catalog variant prices.

### Deliverables & Implementation:
1. **Canonical Refund Idempotency (`packages/payments/service.py`, `packages/fulfillment/service.py`, `packages/fulfillment/reconciliation.py`):**
   - Exported `CANONICAL_REFUND_TYPE = "ORDER_FULFILLMENT_REFUND"`.
   - Unified both `FulfillmentService` and `ReconciliationService` to use `CANONICAL_REFUND_TYPE` and `reference_id=str(order.id)`.
   - Guaranteed that sequential or concurrent refund attempts on the same order result in strictly ONE wallet credit.
2. **Fail-Closed Durable Enqueue (`packages/fulfillment/worker.py`):**
   - In `enqueue(..., persist_db=True)`, database persistence failure now raises `RuntimeError` and aborts before `queue.put()`.
   - Jobs are never stranded exclusively in RAM when durability is requested.
3. **Atomic Worker Job Claim (`packages/fulfillment/worker.py`):**
   - Implemented `claim_job(session, job_id) -> bool` using conditional `UPDATE fulfillment_jobs SET status='RUNNING', locked_at=:now WHERE id=:id AND status='QUEUED'`.
   - In `_process_job()`, only the worker receiving `rowcount == 1` proceeds; competing workers receiving `rowcount == 0` skip immediately.
4. **Frozen Order Total Refund Integrity (`packages/commerce/checkout.py`, `tests/test_phase4_2_integrity.py`):**
   - Verified that refunds strictly credit `order.total_amount` (the frozen checkout amount), invariant even after product catalog price modifications.
5. **Test Suite & Verification (`tests/test_phase4_2_integrity.py`):**
   - Added 5 comprehensive test scenarios covering cross-service refund idempotency, fail-closed DB enqueue, concurrent worker atomic claim, catalog price hike immunity, and worker skip on already-claimed jobs.
   - Total test count: **50/50 passing (100% pass rate)**.
   - Codebase 100% clean under `ruff check .`.

---

## Milestone 8: Phase 4.3 — Database-Enforced Refund Idempotency

- **Date:** 2026-09-15
- **Status:** Completed
- **Commit:** `46ee0fa`

### User Request / Prompt:
> "Phase 4.3 — Database-Enforced Refund Idempotency
> Implement database-enforced idempotency for fulfillment refunds.
> The database itself must enforce the invariant:
> «For one wallet + canonical refund reference, at most ONE refund transaction may exist.»
> 1. Database Constraint: Add PostgreSQL/SQLAlchemy partial unique index uq_refund_idempotency on (wallet_id, reference_type, reference_id) WHERE transaction_type = 'REFUND' AND reference_type IS NOT NULL AND reference_id IS NOT NULL.
> 2. Alembic Migration: Reversible migration with safe legacy duplicate detection (fail cleanly without deleting financial records).
> 3. Concurrency-Safe LedgerService.refund(): Handle UNIQUE constraint violations safely via savepoint/rollback without leaving wallet balance modified.
> 4. Amount Consistency: Raise LedgerIntegrityError on conflicting amount requests.
> 5. Concurrency Tests: Tests A-G covering sequential, concurrent (5-way), mixed services, different refs, different wallets, amount conflict, and non-refund isolation, plus schema verification."

### Deliverables & Implementation:
1. **Schema & Model Definition (`packages/payments/models.py`):**
   - Defined partial unique index `uq_refund_idempotency` on `(wallet_id, reference_type, reference_id)` WHERE `transaction_type = 'REFUND' AND reference_type IS NOT NULL AND reference_id IS NOT NULL`.
   - Configured both `postgresql_where` and `sqlite_where` clauses.
2. **Alembic Migration (`migrations/versions/867840fa9063_add_refund_idempotency_unique_index.py`):**
   - Reversible migration creating and dropping `uq_refund_idempotency` index.
   - Includes safe legacy duplicate detection that halts execution with clear error if duplicates exist, strictly preventing silent data loss.
3. **Concurrency-Safe Ledger (`packages/payments/service.py`):**
   - Atomic savepoint (`session.begin_nested()`) encapsulates balance update and refund insert.
   - On `IntegrityError`, savepoint rollback ensures wallet balance is never left modified.
   - Queries winning transaction, enforces amount equality, and raises `LedgerIntegrityError` if amount mismatches.
   - Unrelated integrity errors are strictly re-raised.
4. **Comprehensive Test Suite (`tests/test_phase4_3_refund_idempotency.py`):**
   - Test A: Sequential idempotency.
   - Test B: True 5-worker concurrent race using WAL mode SQLite with busy timeout.
   - Test C: Concurrent fulfillment & reconciliation services.
   - Test D: Independent reference handling.
   - Test E: Independent multi-wallet isolation.
   - Test F: Amount mismatch raises `LedgerIntegrityError`.
   - Test G: Unaffected non-refund transactions (CREDIT, DEBIT, ADJUSTMENT coexist).
   - Schema Verification: Direct raw insert rejection and metadata index inspection.
   - Total tests: **59/59 passing (100% pass rate)**.
   - Codebase 100% clean under `ruff check .`.


