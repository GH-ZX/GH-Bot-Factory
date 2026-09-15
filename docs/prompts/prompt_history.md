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
- **Status:** In Progress

### User Request / Prompt:
> "add a coding agent map file md , a skill so coding agents knows everything and what happened so easly access everything, inside it all the laws of this project, and inside this skill, how things done, and my prompts, every update should be typed there or in a file so skill redirect to it so this file is mapping all, including my prompts"

### Deliverables & Implementation:
- Created `AGENT_MAP.md` at repository root.
- Created Antigravity Skill `.agents/skills/gh-bot-factory-core/SKILL.md`.
- Created `docs/prompts/prompt_history.md` (this ledger).
- Created `AGENTS.md` and `GEMINI.md` root rules to activate agent guidelines automatically.
