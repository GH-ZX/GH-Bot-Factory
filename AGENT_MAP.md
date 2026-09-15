# GH-Bot-Factory: Master Coding Agent Map & Project Constitution

> **Single Source of Truth for Autonomous Coding Agents**  
> *Last Updated: 2026-09-15 (Phase 4.1 Completed)*

---

## 1. The Sovereign Laws of GH-Bot-Factory

Every AI coding agent working in this repository is strictly bound by these immutable laws. Violating any law constitutes an immediate rollback.

### Law 1: English-Only Communication
- All responses, commit messages, code comments, and documentation must be in **English**.
- Never output answers or reports in Arabic or other languages unless explicitly requested by the user.

### Law 2: Strict Multi-Tenant Data Scoping
- Every business entity (`Order`, `Product`, `Wallet`, `Bot`, `Provider`, `FulfillmentAttempt`, `FulfillmentJobRecord`) **MUST** belong to a `Tenant` (`tenant_id`).
- All queries, filters, and mutations **MUST** be explicitly filtered by `tenant_id`.
- Tenant A must never be able to view, query, or mutate Tenant B's data under any circumstance.
- Telegram identities are NOT global. A user visiting Store A and Store B has separate tenant-scoped profiles (`TenantTelegramUser`).

### Law 3: Zero Secret Leakage
- **NEVER** commit `.env` files, plaintext API keys, Telegram bot tokens, supplier credentials, or database passwords to Git.
- Bot tokens and provider credentials must be referenced via abstract secret keys (e.g. `token_secret_ref`, `credential_secret_ref`) and resolved at runtime via the `SecretStorage` abstraction.
- Never log raw exception traces or credentials in user-facing notifications or public API responses.

### Law 4: Host & Infrastructure Preservation
- **Port 8000 is occupied by Portainer on the host server.** Never bind to or use port 8000.
- Never stop, modify, restart, or delete existing host Docker containers or Cloudflare Tunnels.
- Always work cleanly in `/home/it/Coding/gh-bot-factory` (symlinked from `/opt/gh-bot-factory`).

### Law 5: Double-Entry Financial Invariance, Database-Enforced Refund & Settlement Idempotency
- Customer wallet balances must **NEVER** be updated directly. All balance mutations must pass through `LedgerService` (`credit()`, `debit()`, `refund()`, `settle_payment()`, `adjust()`).
- Every wallet debit must produce an auditable, immutable `LedgerTransaction` record.
- **Database-Enforced Refund Idempotency:** Refund idempotency is database-enforced. Application-level lookup is an optimization; PostgreSQL/SQLite uniqueness (`uq_refund_idempotency` on `(wallet_id, reference_type, reference_id)` WHERE `transaction_type = 'REFUND'`) is the authoritative concurrency guarantee.
- **Database-Enforced Settlement Idempotency:** Partial unique index `uq_settlement_idempotency` on `ledger_transactions(wallet_id, reference_type, reference_id)` WHERE `transaction_type = 'CREDIT' AND reference_type = 'PAYMENT_SETTLEMENT' AND reference_id IS NOT NULL`. Exactly one wallet credit can ever occur for a settled payment intent.
- **Distinct Accounting Separation:** External gateway payment refunds (`PAYMENT_REFUND`) are strictly segregated from order fulfillment failure refunds (`ORDER_FULFILLMENT_REFUND`).
- Under concurrent race conditions (webhook vs user return), the losing transaction hits the database partial unique index, rolls back cleanly via SQL savepoint, verifies amount consistency, and returns the existing transaction without double-crediting.
- If an order permanently fails to fulfill, an automated ledger refund must restore user funds exactly once.

### Law 6: Authoritative Pricing & Payment Amounts (Never Trust the Client)
- Prices and payment amounts sent from client interfaces, web forms, or Telegram inline buttons are untrusted.
- `CheckoutService` **MUST** query the authoritative database record for `ProductVariant.price` scoped to `tenant_id`. Client-supplied amounts are strictly rejected.
- `PaymentService.create_payment_intent()` **MUST** derive amounts strictly from the server-authoritative `Order.total_amount`.

### Law 7: Fulfillment Idempotency & Durability
- Every fulfillment attempt **MUST** specify an explicit attempt-scoped idempotency key:
  $$\text{idempotency\_key} = \text{"order:\{order.id\}:attempt:\{attempt\_number\}"}$$
- Transient network or rate limit errors must transition to `RETRYING` or `UNKNOWN`, never premature `FAILED`.
- **Never notify the customer that their wallet was refunded unless the ledger refund transaction has already succeeded.**
- Background jobs must be stored durably in the database (`FulfillmentJobRecord`) to survive process crashes and power loss. On restart, workers must call `recover_pending_jobs()`.

### Law 8: Cryptographic Telegram Mini App Authentication Boundary
- Telegram Mini App `initData` must be cryptographically validated server-side using Telegram's official HMAC-SHA256 signature algorithm against `HMAC_SHA256("WebAppData", bot_token)`.
- Rejects expired `auth_date` (> 86400s) and future timestamps (> 300s).
- Tenant scoping must be derived authoritatively from the owning `Bot` record. Client-supplied tenant overrides are strictly rejected.

### Law 9: Quality & Verification Gates
- Before any milestone commit or push:
  1. All database migrations must be applied cleanly (`alembic upgrade head`).
  2. The test suite must pass with 100% success (`.venv/bin/pytest -v`).
  3. The codebase must be completely clean of linter errors (`.venv/bin/ruff check .`).

### Law 10: Version Control & Remote Sync
- Every coherent milestone must be committed to Git and pushed to remote `git@github.com:GH-ZX/GH-Bot-Factory.git` on branch `main` using SSH key `~/.ssh/id_ed25519_ghzx`.

---

## 2. Architecture & Codebase Map

```
GH-Bot-Factory Root
├── apps/
│   ├── api/                  # FastAPI web server, health checks, webhook endpoints
│   │   └── v1/               # Version 1 modular routers (/payments, /auth)
├── packages/
│   ├── core/                 # Shared Base database model, UUID/Timestamp mixins, global exceptions
│   ├── tenants/              # Tenant, User, Membership (RBAC: OWNER, ADMIN, STAFF, CUSTOMER)
│   ├── commerce/             # Category, Product, ProductVariant, Order, OrderItem, StateMachine, Checkout
│   ├── payments/             # PaymentIntent, PaymentTransaction, PaymentProviderConfig, Webhooks, Ledger
│   ├── telegram/             # Aiogram 3 multi-tenant runtime, Bot Registry, Middlewares, Routers, TMA Auth
│   ├── providers/            # Provider Protocol, Client Registry, Mock & Digital Providers, ProviderRouter
│   ├── fulfillment/          # FulfillmentService, Worker (durable queue), ReconciliationService, Models
│   └── notifications/        # Decoupled notification transport system (Telegram/Audit)
├── migrations/               # Alembic database schema revisions
├── tests/                    # Pytest async test suite (86/86 passing)
├── docs/
│   ├── architecture/         # System architecture deep-dives & sequence diagrams
│   ├── decisions/            # Architecture Decision Records (ADR-001 through ADR-006)
│   └── prompts/              # Complete historical archive of all user prompts and instructions
└── .agents/
    └── skills/               # Antigravity agent skills repository
```

### Module Responsibilities & Core Classes:
| Package / App | Primary Classes / Routers | Key Role |
| :--- | :--- | :--- |
| `packages.core` | `Base`, `UUIDMixin`, `TimestampMixin` | Foundation DB base, async engine, session makers |
| `packages.tenants` | `Tenant`, `User`, `Membership`, `Role` | Tenant scoping, customer authentication, RBAC |
| `packages.commerce` | `Order`, `OrderItem`, `Product`, `ProductVariant`, `OrderStateMachine`, `CheckoutService` | Catalog, legal state transitions, cart checkout |
| `packages.payments` | `PaymentIntent`, `PaymentTransaction`, `PaymentProviderConfig`, `PaymentWebhookEvent`, `PaymentService`, `PaymentProviderRegistry`, `PaymentReconciliationService`, `Wallet`, `LedgerTransaction`, `LedgerService` | Provider-agnostic payment gateway abstraction, durable PaymentIntents, webhook security, settlement idempotency, double-entry wallet ledger |
| `packages.telegram` | `Bot`, `TenantTelegramUser`, `TelegramMiniAppAuthService`, `BotRuntimeManager`, `TenantResolutionMiddleware` | Multi-bot runtime, secret storage, tenant resolution, FSM isolation, TMA initData HMAC auth |
| `packages.providers` | `Provider`, `ProviderCredential`, `ProviderProductMapping`, `ProviderRouter`, `MockProvider` | Supplier protocol, failover routing, credential safety |
| `packages.fulfillment` | `FulfillmentAttempt`, `FulfillmentJobRecord`, `FulfillmentService`, `FulfillmentWorker`, `ReconciliationService` | Durable execution, retry backoff, crash recovery, out-of-band reconciliation |
| `packages.notifications`| `NotificationService`, `NotificationPayload`, `MockNotificationTransport` | Asynchronous post-checkout customer alerting |
| `apps.api.v1` | `payments_router`, `auth_router` | Multi-client REST endpoints: Payment intent lifecycle, webhook ingestion, TMA HMAC validation |

---

## 3. Developer & Coding Agent Playbooks

### Playbook A: Adding Database Models & Migrations
1. Define the model in the appropriate `packages/<domain>/models.py` inheriting from `Base, UUIDMixin, TimestampMixin`.
2. Ensure every model includes `tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)`.
3. Import the new model in `packages/core/models.py` so Alembic detects it.
4. **SQLite Batch Mode Rule:** Because SQLite does not support standard `ALTER TABLE`, all migrations must use batch operations:
   ```python
   with op.batch_alter_table("table_name", schema=None) as batch_op:
       batch_op.add_column(...)
   ```
5. Generate and apply migration:
   ```bash
   .venv/bin/alembic revision --autogenerate -m "description"
   .venv/bin/alembic upgrade head
   ```

### Playbook B: Testing with `pytest-asyncio` & In-Memory SQLite
- Tests run against an isolated in-memory SQLite database (`sqlite+aiosqlite:///:memory:`).
- **StaticPool Requirement:** To ensure multiple sessions (e.g. background worker threads or test steps) share the exact same schema in memory, `conftest.py` uses `poolclass=StaticPool` and `connect_args={"check_same_thread": False}`.
- Test command:
  ```bash
  .venv/bin/pytest -v
  ```

### Playbook C: Code Formatting & Linting
- Antigravity uses `ruff` for all Python linting and imports sorting.
  ```bash
  .venv/bin/ruff check . --fix
  .venv/bin/ruff check .
  ```

### Playbook D: Git Milestone Commit & Push
- Never push broken tests or lint errors.
- Always use the dedicated SSH key:
  ```bash
  GIT_SSH_COMMAND="ssh -i ~/.ssh/id_ed25519_ghzx" git push origin main
  ```

---

## 4. Living Milestone Ledger & Prompt History

All verbatim user prompts, architectural requirements, and commit records are cataloged in detail inside [`docs/prompts/prompt_history.md`](file:///home/it/Coding/gh-bot-factory/docs/prompts/prompt_history.md).

### Chronological Summary:
1. **Phase 1 (Foundation):** Scaffolding, FastAPI health checks, uv virtualenv. Commit: `913a48e`.
2. **Phase 2 (Commerce Core & Ledger):** Multi-tenancy, RBAC, strict state machine, double-entry wallet ledger. Commit: `714e815`.
3. **Phase 3 (Telegram Runtime):** Multi-tenant Aiogram 3 bot manager, secret refs, tenant resolution middleware, isolated FSM. Commit: `8520386`.
4. **Phase 4 (Provider Engine & Fulfillment):** Supplier protocol, client registry, resilient router, checkout & fulfillment services. Commits: `5eae8c5`, `d9a8baa`, `5c992a2`, `15a4882`.
5. **Phase 4.1 (Production Hardening):** `UNKNOWN`/`RETRYING` states, durable DB queue (`fulfillment_jobs`), startup crash recovery, strict refund idempotency, multi-item fulfillment, zero raw exception leaks. Commit: `bcee3a1`.
6. **Milestone 6 (Coding Agent Map & Skill):** Creation of `AGENT_MAP.md`, `.agents/skills/gh-bot-factory-core/SKILL.md`, and `docs/prompts/prompt_history.md`. Commit: `aa0f7ab`.
7. **Milestone 7 (Phase 4.2 Fulfillment Integrity Hardening):** Canonical refund idempotency (`CANONICAL_REFUND_TYPE = "ORDER_FULFILLMENT_REFUND"`), fail-closed durable enqueue, atomic conditional job claim, and frozen catalog refund protection. 50/50 tests passing. Commit: `6d750b7`.
8. **Milestone 8 (Phase 4.3 Database-Enforced Refund Idempotency):** Schema-level partial unique index (`uq_refund_idempotency`), Alembic migration `867840fa9063` with legacy duplicate safety check, concurrency-safe `LedgerService.refund()`, amount mismatch guard (`LedgerIntegrityError`), and 59/59 tests passing. Commit: `79b5ba0`.
9. **Milestone 9 (Phase 5 Payment Infrastructure & Multi-Client API Foundation):** Provider-agnostic payment gateway abstraction (`packages.payments`), durable `PaymentIntent` with server-authoritative amounts, `PaymentStateMachine`, `PaymentProvider` protocol and registry, database-enforced settlement idempotency (`uq_settlement_idempotency`), distinct `PAYMENT_REFUND` accounting, webhook verification and deduplication, `PaymentReconciliationService`, server-side Telegram Mini App HMAC-SHA256 authentication (`TelegramMiniAppAuthService`), FastAPI REST endpoints (`/api/v1/payments`, `/api/v1/auth`), reversible Alembic migration `a8d5f418df6f`, and 86/86 tests passing. Prompts handled: initial Phase 5 specification and continuation directive *"sorry forvunteruption, use more agents and resume"*. Commit: Pending.

---

## 5. Instructions for Future AI Coding Agents

> [!CAUTION]
> **WHEN YOU RECEIVE A NEW USER REQUEST:**
> 1. **Consult this document first:** Review the Sovereign Laws in Section 1 before modifying or designing any code.
> 2. **Inspect existing architecture:** Do not reinvent models, state machines, or middlewares. Extend the existing patterns in `packages/`.
> 3. **Record the prompt:** Append the user's prompt verbatim into [`docs/prompts/prompt_history.md`](file:///home/it/Coding/gh-bot-factory/docs/prompts/prompt_history.md) under a new Milestone section.
> 4. **Update this map:** If you introduce new packages, database tables, or architectural decisions, update Section 2, Section 4, and create corresponding ADRs in `docs/decisions/`.
> 5. **Run all verification gates:** Never consider a turn finished until `pytest -v` and `ruff check .` pass cleanly.
