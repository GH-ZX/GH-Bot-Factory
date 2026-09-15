# GH-Bot-Factory: Master Coding Agent Map & Project Constitution

> **Single Source of Truth for Autonomous Coding Agents**  
> *Last Updated: 2026-09-15 (Phase 8.7 Bot Factory Release Candidate)*

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
- Admin/provider APIs must never return resolved secrets or secret references. Supplier execution **and reconciliation** must resolve credentials only at the runtime adapter boundary. Provider-controlled endpoint settings that could redirect credential-bearing requests must be rejected or strictly allowlisted.
- Never log raw exception traces or credentials in user-facing notifications or public API responses.
- JWT signing must never use a repository-known fallback key. `JWT_SECRET_KEY` is mandatory for runtime token signing and must be at least 32 bytes; `SECRET_KEY` is not an accepted JWT fallback.

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
- `PaymentService.create_payment_intent()` **MUST** derive order-payment amounts strictly from the server-authoritative `Order.total_amount`.
- Wallet top-up amounts are customer-selected inputs, but **MUST** be normalized and validated against tenant-owned provider min/max/currency policy before a `WALLET_TOPUP` intent is created. Provider settlement amount/currency must exactly match the stored intent.
- Storefront checkout request models must reject client-supplied price, amount, tenant, or user identity fields.
- Mini App checkout retries **MUST** use database-backed order idempotency on `(tenant_id, user_id, checkout_idempotency_key)` before any wallet debit. Reusing a key with a different cart/recipient fingerprint must be rejected.

### Law 7: Fulfillment Idempotency & Durability
- Every fulfillment attempt **MUST** specify an explicit attempt-scoped idempotency key:
  $$\text{idempotency\_key} = \text{"order:\{order.id\}:attempt:\{attempt\_number\}"}$$
- Transient network or rate limit errors must transition to `RETRYING` or `UNKNOWN`, never premature `FAILED`.
- **Never notify the customer that their wallet was refunded unless the ledger refund transaction has already succeeded.**
- Background jobs must be stored durably in the database (`FulfillmentJobRecord`) to survive process crashes and power loss. On restart, workers must call `recover_pending_jobs()`.
- Manual operator requeue **MUST fail closed**: never replay an `UNKNOWN` attempt, a job with an upstream external order ID, a terminal/refunded order, or an order with a canonical fulfillment refund. Operator requeue must use an atomic database transition and be audit logged.
- Manual reconciliation from the admin client must be targeted to one authenticated-tenant order; an operator click must not trigger tenant-wide reconciliation side effects.

### Law 8: Cryptographic Telegram Mini App Authentication Boundary
- Telegram Mini App `initData` must be cryptographically validated server-side using Telegram's official HMAC-SHA256 signature algorithm against `HMAC_SHA256("WebAppData", bot_token)`.
- Rejects expired `auth_date` (> 86400s) and future timestamps (> 300s).
- Tenant scoping must be derived authoritatively from the owning `Bot` record. Client-supplied tenant overrides are strictly rejected.

### Law 8A: Wallet Top-up Reversal Safety
- `WALLET_TOPUP` refunds **MUST NOT** reuse order-payment or fulfillment-refund accounting.
- The corresponding internal wallet value **MUST** be reserved/debited idempotently before contacting an external refund API.
- A provider timeout/ambiguous response after reservation **MUST NOT** restore customer value automatically; transition to reconciliation/manual review and keep the reservation fail-closed.
- Provider refund retries must be proven safe/idempotent for that adapter before automated retry is enabled.
- Database uniqueness, not in-memory checks, is the final authority preventing duplicate reversal debits.
- Provider-originated Telegram Stars reversals/chargebacks **MUST** be correlated against authoritative payment intent, amount, and user identity. If the corresponding wallet value has already been spent, the wallet must be frozen and routed to manual review rather than allowing additional debits.

### Law 9: Quality & Verification Gates
- `make verify` is the canonical release command and MUST pass before any milestone commit or push.
- The fast gate includes Ruff, compileall, handoff consistency, non-PostgreSQL pytest, Admin/Mini App JavaScript syntax, `git diff --check`, and `pip check`.
- The PostgreSQL gate requires a real PostgreSQL database, applies `alembic upgrade head`, requires `alembic check` to report no drift, and executes all `pytest.mark.postgres` concurrency tests.
- SQLite-only success is never sufficient evidence for PostgreSQL row-locking, partial-index, transaction, or worker-claim invariants.
- Missing dependencies/services are a blocked gate, never a green gate.

### Law 10: Version Control & Remote Sync
- Every coherent milestone must be committed to Git and pushed to remote `git@github.com:GH-ZX/GH-Bot-Factory.git` on branch `main` using SSH key `~/.ssh/id_ed25519_ghzx`.

### Law 11: The 5 Security Invariants of API Authentication & Authorization Hardening
1. **Identity Law:** *«Client-supplied user/tenant IDs are never authoritative.»* Clients cannot supply raw identity headers (`X-Tenant-ID`, `X-User-ID`), request body fields, or query parameters to assert user identity. Identity is derived strictly from server-verified Bearer JWT access tokens.
2. **Authorization Law:** *«Every customer API operation is authorized against the authenticated principal.»* All protected operations are authorized strictly against [`AuthenticatedPrincipal`](file:///home/it/Coding/gh-bot-factory/packages/core/auth.py#L64). Customer-role callers cannot view, cancel, or reconcile another customer's payment intents or orders.
   - Tenant roles and membership active state are re-derived from the live `Membership` row on every protected request; JWT role claims are not the authorization authority. ADMIN cannot grant/modify ADMIN or OWNER, and a membership mutation must never remove the tenant's last active OWNER.
3. **Mini App Law:** *«Telegram "initData" is authenticated cryptographically before deriving identity.»* Telegram Mini App query strings must be validated using official HMAC-SHA256 signatures against the bot's secret token before user provisioning or token issuance.
4. **Webhook Law:** *«Payment gateway webhooks are authenticated with provider-specific cryptographic verification and tenant/provider binding.»* External payment webhooks operate under a distinct trust boundary. They are authenticated using provider-specific cryptographic signature verification against the tenant's configured secret and deduplicated on `uq_webhook_tenant_provider_event`. Customer JWTs are never accepted for webhooks.
5. **Tenant Law:** *«Authenticated tenant context cannot be overridden by request headers/body/query parameters.»* All operations execute strictly within `principal.tenant_id`. Client-supplied tenant IDs that contradict the authenticated principal or bot association are rejected with `TenantAccessViolationError` or `403 Forbidden`.

### Law 12: Maintainability & Agent Traceability
- `docs/operations/CURRENT_STATE.md` is the authoritative short-form resume checkpoint and MUST match the working tree after every milestone.
- `docs/operations/CHANGELOG_AGENT.md` records concise technical handoff entries; `docs/prompts/prompt_history.md` preserves user intent; ADRs preserve architectural rationale.
- Every milestone must update the current state, changelog, roadmap, prompt history, and any affected architecture/agent maps before it is considered complete.
- Verification claims must distinguish canonical, runnable, and blocked gates. Never turn missing dependencies into a false success claim.

### Law 13: Production Readiness & Release Evidence
- Migrations, API, worker, and bot runtime MUST use the same immutable application image revision in production.
- Production/staging processes run non-root with read-only root filesystems; writable state belongs in PostgreSQL, Redis, declared volumes, or bounded tmpfs.
- Liveness and readiness are distinct. Worker/bot-runtime health requires a live process heartbeat, not only reachable dependencies.
- Production/staging sensitive-route rate limiting is Redis-backed and fail-closed; never silently downgrade to process-local limits in a multi-replica environment.
- PostgreSQL is the durable business source of truth. Backups must be restorable, and a backup is not considered valid until a restore drill proves it.
- A production release is not merely a commit: it is Git SHA + migration head + canonical verification evidence + immutable image identity + required staging evidence.
- Phase 8 provisioning must preserve all Phase 7.9 gates; scaling is never a reason to bypass release evidence.

### Law 14: Durable Bot Provisioning & Telegram Identity Authority
- Admin/API callers submit only a `token_secret_ref`; plaintext Telegram tokens must never enter Bot/Job rows, audit details, browser responses, or logs.
- Bot provisioning is a durable worker workflow. HTTP routes may enqueue desired state but must not perform Telegram provisioning inline.
- Telegram `getMe().id` / `telegram_bot_id` is the global identity and ownership authority. Username, display name, and secret-reference strings are not ownership authority.
- `(tenant_id, idempotency_key)` plus request fingerprint binds retries to one provisioning intent. Reusing the key for different intent is forbidden.
- Cross-tenant ownership conflicts fail closed. Concurrent identity races defer to database uniqueness and must converge through retry/re-read, never duplicate a Bot row.
- Bot runtime continuously reconciles PostgreSQL desired state; create/update/disable/deleted/crashed polling state must converge without manual process restart.

### Law 15: Versioned Template & Verified Bot-Branding Context
- Normal Bot Factory provisioning must use a server-owned template key/version plus allow-listed overrides; browser-authored raw configuration is a legacy compatibility path, not the preferred operating model.
- A template version is immutable in meaning. Material default changes require a new version; existing bots are never silently rewritten.
- Persist `_factory.template_key` and `_factory.template_version` in bot config so template provenance is inspectable without external state.
- Branding URLs must be HTTPS without embedded credentials, modules must be allow-listed, and config must remain secret-free.
- Telegram Mini App authentication signs the verified internal `bot_id` into the access token. Per-bot storefront branding must derive from this signed claim, never from an authoritative browser-supplied bot ID.
- Post-provision template/branding mutations preserve the credential reference, require ADMIN/OWNER, and must be AuditLog-backed.

---

## 2. Architecture & Codebase Map

```
GH-Bot-Factory Root
├── apps/
│   ├── api/                  # FastAPI web server, health checks, webhook endpoints
│   │   ├── deps.py           # Dependency injection: get_current_principal, require_roles
│   │   └── v1/               # Version 1 modular routers (/payments, /auth, /storefront)
│   ├── miniapp/              # Static Telegram Mini App storefront client
│   ├── admin/                # Static tenant operations, analytics, audit, and Bot Factory console
│   └── worker/               # Durable fulfillment, payment reversal, reconciliation, and provisioning workers
├── packages/
│   ├── core/                 # DB base, auth, config, observability, health, security/rate limits
│   │   └── auth.py           # JWT signing/verification, AuthenticatedPrincipal, AuthSource
│   ├── tenants/              # Tenant, User (token_version), Membership (RBAC: OWNER, ADMIN, STAFF, CUSTOMER)
│   ├── commerce/             # Category, Product, ProductVariant, Order, OrderItem, StateMachine, Checkout
│   ├── payments/             # PaymentIntent, PaymentTransaction, PaymentProviderConfig, Webhooks, Ledger
│   ├── telegram/             # Aiogram 3 multi-tenant runtime, desired-state reconciliation, routers, TMA auth
│   ├── factory/              # Durable provisioning, versioned templates/branding, Telegram identity verification
│   ├── providers/            # Provider Protocol, Client Registry, Mock & Digital Providers, ProviderRouter
│   ├── fulfillment/          # FulfillmentService, Worker (durable queue), ReconciliationService, Models
│   ├── analytics/            # Read-only tenant analytics aggregation boundary
│   └── notifications/        # Decoupled notification transport system (Telegram/Audit)
├── migrations/               # Alembic revisions, including checkout idempotency c41d6f8e2a10
├── tests/                    # Fast async suite plus tests/postgres production-engine concurrency invariants
├── docs/
│   ├── architecture/         # System architecture deep-dives & sequence diagrams
│   ├── decisions/            # Architecture Decision Records (ADR-001 through ADR-017)
│   └── prompts/              # Complete historical archive of all user prompts and instructions
└── .agents/
    └── skills/               # Antigravity agent skills repository
```

### Module Responsibilities & Core Classes:
| Package / App | Primary Classes / Routers | Key Role |
| :--- | :--- | :--- |
| `packages.core` | `Base`, `UUIDMixin`, `TimestampMixin`, `AuthenticatedPrincipal`, `AuthTokenService`, `AuthSource` | Foundation DB base, async engine, cryptographic JWT access tokens, principal abstraction |
| `packages.tenants` | `Tenant`, `User` (with `token_version`), `Membership`, `Role` | Tenant scoping, customer authentication, RBAC, instant session revocation |
| `packages.commerce` | `Order`, `OrderItem`, `Product`, `ProductVariant`, `OrderStateMachine`, `CheckoutService` | Catalog, legal state transitions, cart checkout |
| `packages.payments` | `PaymentIntent`, `PaymentIntentPurpose`, `PaymentTransaction`, `PaymentProviderConfig`, `PaymentWebhookEvent`, `PaymentService`, `PaymentProviderRegistry`, `PaymentReconciliationService`, `Wallet`, `LedgerTransaction`, `LedgerService` | Provider-agnostic order payments and wallet top-ups, durable PaymentIntents, webhook security, settlement idempotency, double-entry wallet ledger |
| `packages.telegram` | `Bot`, `TenantTelegramUser`, `TelegramMiniAppAuthService`, `BotRuntimeManager`, `TenantResolutionMiddleware` | Multi-bot runtime, secret storage, tenant resolution, FSM isolation, TMA initData HMAC auth, privileged `/admin` Web App entry |
| `packages.providers` | `Provider`, `ProviderCredential`, `ProviderProductMapping`, `ProviderRouter`, `MockProvider` | Supplier protocol, failover routing, credential safety |
| `packages.fulfillment` | `FulfillmentAttempt`, `FulfillmentJobRecord`, `FulfillmentService`, `FulfillmentWorker`, `ReconciliationService` | Durable execution, DB polling/atomic claim, retry backoff, crash recovery, out-of-band reconciliation |
| `packages.notifications`| `NotificationService`, `NotificationPayload`, `MockNotificationTransport` | Asynchronous post-checkout customer alerting |
| `packages.analytics` | `AdminAnalyticsService` | Read-only, tenant-scoped, currency-safe operational analytics over authoritative domain tables |
| `apps.api.deps` | `get_current_principal`, `require_roles`, `require_staff_or_above` | Bearer JWT verification, session revocation check, tenant membership and role enforcement |
| `apps.api.v1` | `payments_router`, `auth_router`, `storefront_router`, `admin_router`, `admin_analytics_router` | Multi-client REST endpoints: Bearer JWT issuance, customer-isolated payments, webhook verification, tenant-scoped storefront, operations, analytics, and audit |
| `apps.miniapp` | Static HTML/CSS/JS storefront | Telegram WebView presentation layer; in-memory Bearer session, catalog/cart/orders UX, no authoritative prices or identity |
| `apps.admin` | Static HTML/CSS/JS operator console | Telegram-authenticated tenant operations UI; RBAC-aware operations plus guided Bot Factory template/branding provisioning |
| `apps.worker` | `run_worker`, `FulfillmentWorker` | Dedicated process for recovery, DB polling, atomic job claim, supplier execution, retry/dead-letter handling |

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
9. **Milestone 9 (Phase 5 Payment Infrastructure & Multi-Client API Foundation):** Provider-agnostic payment gateway abstraction (`packages.payments`), durable `PaymentIntent` with server-authoritative amounts, `PaymentStateMachine`, `PaymentProvider` protocol and registry, database-enforced settlement idempotency (`uq_settlement_idempotency`), distinct `PAYMENT_REFUND` accounting, webhook verification and deduplication, `PaymentReconciliationService`, server-side Telegram Mini App HMAC-SHA256 authentication (`TelegramMiniAppAuthService`), FastAPI REST endpoints (`/api/v1/payments`, `/api/v1/auth`), reversible Alembic migration `a8d5f418df6f`, and 86/86 tests passing. Prompts handled: initial Phase 5 specification and continuation directive *"sorry forvunteruption, use more agents and resume"*. Commit: `334ce90`.
10. **Milestone 10 (Phase 5.1 API Authentication & Authorization Hardening):** Eliminated raw identity headers (`X-Tenant-ID`, `X-User-ID`) in favor of server-signed Bearer JWT access tokens (`AuthTokenService`) and authoritative [`AuthenticatedPrincipal`](file:///home/it/Coding/gh-bot-factory/packages/core/auth.py#L64). Implemented FastAPI dependency pipeline [`get_current_principal`](file:///home/it/Coding/gh-bot-factory/apps/api/deps.py#L23) with user/tenant activation verification and instant session revocation via `users.token_version` (Alembic migration [`b7e3a912f45c_add_user_token_version.py`](file:///home/it/Coding/gh-bot-factory/migrations/versions/b7e3a912f45c_add_user_token_version.py)). Bound Telegram Mini App HMAC authentication to Bearer JWT issuance. Enforced customer ownership isolation across all payment intent endpoints (preventing cross-user intent access/tampering). Enforced the 5 Security Invariants and established strict architectural separation between customer session authentication and payment gateway webhook cryptographic authentication. Documented in [`docs/decisions/ADR-007-api-authentication-hardening.md`](file:///home/it/Coding/gh-bot-factory/docs/decisions/ADR-007-api-authentication-hardening.md). Commit: `0628a31`.
11. **Milestone 11 (Phase 5.1.1 JWT Signing Secret Hardening):** Removed the repository-known JWT fallback secret and the `SECRET_KEY` JWT fallback, made `JWT_SECRET_KEY` mandatory for runtime token signing, enforced a minimum 32-byte signing key, removed raw PyJWT error details from public malformed-token responses, and added six focused security tests for fail-closed configuration, rotation invalidation, and secret non-disclosure. Implementation is complete in the working tree; canonical full-suite and Ruff verification must still be executed in an environment with the declared dev dependencies installed before milestone commit/push.
12. **Milestone 12 (Phase 6.0-6.1 Telegram Mini App Storefront & Durable Fulfillment Boundary):** Added a mobile-first static Telegram Mini App at `/miniapp/`, authenticated through existing Telegram `initData` validation and Bearer JWT issuance; added tenant-scoped `/api/v1/storefront` bootstrap, catalog, order, and wallet checkout APIs; restricted public tenant/product metadata; enforced customer order ownership; rejected extra client checkout fields; expanded `CheckoutService` to multi-line authoritative pricing; and added database-backed checkout idempotency (`checkout_idempotency_key`, request fingerprint, `uq_order_checkout_idempotency`, migration `c41d6f8e2a10`) so retry/double-submit paths cannot double-debit a wallet. Storefront checkout now stages a durable `FulfillmentJobRecord` in the same transaction as the paid order/wallet debit and never performs supplier execution on the HTTP path. `FulfillmentWorker` gained DB polling while retaining atomic job claims, and `apps.worker.main` provides the dedicated worker process. Docker development wiring now runs the API on port 8010 and a separate worker service. Migration upgrade/downgrade/upgrade passed and 113/113 tests runnable without `aiogram` passed. Eight Aiogram-dependent tests and canonical Ruff remain pending because those dependencies are unavailable in the current execution environment; no milestone commit/push is permitted until the canonical gate passes.
13. **Milestone 13 (Phase 6.1.1 Telegram Live Launch Integration):** Added public HTTPS Mini App URL configuration, per-bot launch URL generation, private-chat Web App buttons, automatic persistent Telegram menu-button configuration, an executable `apps.bot_runtime` process, Docker Compose migration/health ordering, a safe bot-registration CLI, and a production-oriented live-launch runbook. Hardened customer identity continuity so the same Telegram user is reused across multiple bots owned by one tenant and legacy users receive a `telegram_id` backfill. New launch/identity tests pass, bringing the non-Aiogram runnable suite to 116/116. Canonical Aiogram/Ruff verification remains gated by unavailable dependencies in this execution environment.
14. **Milestone 14 (Phase 6.2 Wallet Top-up UX):** Added explicit `PaymentIntentPurpose` with `WALLET_TOPUP`, nullable `order_id` only for non-order funding, persisted HTTPS-only provider checkout URLs, tenant-owned top-up amount/currency policy, request-bound idempotency, synchronous/webhook/reconciliation settlement convergence, and dedicated Mini App Add Funds UX with pending intent recovery and wallet refresh. Top-up settlement continues to rely on `uq_settlement_idempotency` for exactly-once wallet credit. Unsafe reuse of the legacy order-payment refund path is blocked for top-ups. Migration `d84f2a6c9b31` upgrades/downgrades cleanly on empty top-up data and refuses destructive downgrade once top-up financial records exist. Non-Aiogram runnable suite: 123/123.
15. **Milestone 15 (Phase 6.2.1-6.2.2 Telegram Stars Native Payments & Durable Refund Reversal):** Registered a production `telegram_stars` adapter using Telegram Bot API native invoices (`XTR`), server-authoritative pre-checkout validation, authoritative `successful_payment` settlement, native Mini App invoice opening, Terms/support integration, transaction reconciliation, and secret-reference reuse of tenant bot credentials. Added `WalletTopUpReversal` plus migration `e91a3b7c4d52`, a database-idempotent `WALLET_TOPUP_REVERSAL` ledger debit, reserve-before-refund semantics, safe provider retry handling, a durable reversal worker with crash recovery/backoff/manual-review states, and staff APIs to request/inspect reversals. Fixed cross-bot Telegram identity reuse in the actual tenant middleware. New Stars/reversal tests pass; full non-Aiogram runnable suite: 129/129. Alembic clean upgrade and `e91 -> d84 -> e91` migration cycle passed. Canonical Aiogram/Ruff gate remains pending because those dependencies are unavailable in this execution environment; no commit/push is permitted yet.
16. **Milestone 16 (Phase 6.2.3 External Telegram Stars Reversal Reconciliation):** Added authenticated periodic `getStarTransactions` scanning, durable/deduplicated `PaymentReconciliationEvent` audit records, provider-side intent/amount/user correlation, exact-once mirroring of unrequested external reversals into `WALLET_TOPUP_REVERSAL`, and crash recovery for intentional refunds observed upstream before their local completion commit. If an external reversal arrives after the customer has spent value, the wallet is frozen and the case is marked `MANUAL_REVIEW`; normal wallet debit rejects frozen wallets. Staff can list reconciliation events and locally resolve external reversals once sufficient value is restored, without calling the provider again. Migration `f02c4d8e5a63` adds reconciliation events and database uniqueness for reversal payment transactions. Focused Stars tests: 10/10; full non-Aiogram runnable suite: 133/133. Migration clean upgrade and `f02 -> e91 -> f02` cycle passed. Canonical Aiogram/Ruff gate remains pending.


---

## 5. Instructions for Future AI Coding Agents

> [!CAUTION]
> **WHEN YOU RECEIVE A NEW USER REQUEST:**
> 1. **Consult this document first:** Review the Sovereign Laws in Section 1 before modifying or designing any code.
> 2. **Inspect existing architecture:** Do not reinvent models, state machines, or middlewares. Extend the existing patterns in `packages/`.
> 3. **Record the prompt:** Append the user's prompt verbatim into [`docs/prompts/prompt_history.md`](file:///home/it/Coding/gh-bot-factory/docs/prompts/prompt_history.md) under a new Milestone section.
> 4. **Update this map:** If you introduce new packages, database tables, or architectural decisions, update Section 2, Section 4, and create corresponding ADRs in `docs/decisions/`.
> 5. **Run all verification gates:** Never consider a turn finished until `pytest -v` and `ruff check .` pass cleanly.


## Milestone 17: Phase 6.3 — Storefront Product UX

- **Date:** 2026-09-15
- **Status:** Implemented in working tree; canonical Aiogram/Ruff verification pending
- **Continuation:** Completed immediately after the Phase 6.2.3 Stars reconciliation hardening.

### Implementation

1. Extended `GET /api/v1/storefront/catalog` with tenant-scoped `q`, `available`, `offset`, and bounded `limit` parameters.
2. Search covers product title/description plus active variant title/SKU without exposing inactive/deleted products or variants.
3. Added deterministic title/ID ordering and response pagination metadata (`total`, `has_more`, `next_offset`).
4. Added debounced Mini App search, category and availability filtering, and explicit load-more pagination.
5. Added a client-side variant cache so cart entries remain stable when the visible catalog page/filter changes; checkout pricing remains server-authoritative.
6. Added stock-aware variant presentation and sold-out guards, SKU visibility, delivery hints, skeleton loading, contextual empty/error states, and offline recovery.
7. Added stale-response protection so slower catalog requests cannot overwrite newer searches.

### Verification

- Storefront tests: **11/11 passed**.
- Full repository suite excluding only the two modules importing unavailable `aiogram`: **135/135 passed**.
- `python -m compileall -q apps packages scripts tests migrations`: passed.
- `node --check apps/miniapp/static/app.js`: passed.
- `git diff --check`: passed.
- No database migration was required for Phase 6.3; migration head remains `f02c4d8e5a63`.
- Canonical Aiogram tests and Ruff remain pending because those dependencies are unavailable in this execution environment; no commit/push was created.


## Milestone 18: Phase 7.0 — Admin Client Tenant Operations Vertical Slice

- **Date:** 2026-09-15
- **Status:** Implemented in working tree; canonical Aiogram/Ruff verification pending
- **Continuation:** First Phase 7 slice after Phase 6.3 storefront product UX.

### Implementation

1. Added a Telegram-aware static admin client at `/admin/`, reusing the existing `initData` authentication exchange and Bearer JWTs.
2. Added `/api/v1/admin` with tenant-scoped operations bootstrap, category/product/variant management, order visibility, and payment reconciliation review.
3. Added `require_manager_or_above`: STAFF can read operational data while MANAGER/ADMIN/OWNER can mutate the catalog.
4. Product/category/variant mutations write `AuditLog` records identifying the actor, resource and changed fields.
5. All resource ownership is derived from `AuthenticatedPrincipal`; foreign-tenant category/product/order/reconciliation data is hidden or rejected.
6. Order administration surfaces the latest durable `FulfillmentJobRecord` status without moving supplier execution back onto the HTTP path.
7. Added ADR-012 documenting the admin authentication, RBAC and tenant trust boundary.

### Verification

- Phase 7 admin tests: **7/7 passed**.
- Full repository suite excluding only the two modules importing unavailable `aiogram`: **142/142 passed**.
- `python -m compileall -q apps packages scripts tests migrations`: passed.
- `node --check apps/admin/static/app.js`: passed.
- `git diff --check`: passed.
- No database migration required; migration head remains `f02c4d8e5a63`.
- Canonical Aiogram tests and Ruff remain pending because those dependencies are unavailable in this execution environment; no commit/push was created.


## Milestone 19: Phase 7.1 — Fulfillment Operations Center

- **Date:** 2026-09-15
- **Status:** Implemented in working tree; canonical Aiogram/Ruff verification pending

### Implementation

1. Added tenant-scoped fulfillment job listing/detail APIs and Admin UI dead-letter operations view.
2. Added durable job failure classification, manual requeue counter, last requeue timestamp, and actor metadata with migration `19c7d8e41f02`.
3. Added `require_admin_or_owner` for high-risk operational mutations; STAFF+ retains read-only operational visibility.
4. Added fail-closed `evaluate_manual_requeue()` invariants: no terminal/refunded order, no canonical fulfillment refund, no upstream external order ID, no UNKNOWN/PROCESSING/SUCCEEDED latest attempt, and no permanent provider failure replay.
5. Added atomic conditional `DEAD_LETTER -> QUEUED` database transition so concurrent operator retries cannot both win.
6. Added targeted `ReconciliationService.reconcile_order()` and Admin action so one operator click cannot reconcile unrelated tenant orders.
7. Added AuditLog entries for manual requeue and reconciliation operations.
8. Added ADR-013 and updated fulfillment architecture/roadmap/README.

### Verification

- Phase 7.0 + Phase 7.1 focused tests: **12/12 passed**.
- Full repository suite excluding only the two modules importing unavailable `aiogram`: **147/147 passed**.
- Alembic clean upgrade through `19c7d8e41f02 (head)`: passed.
- `python -m compileall -q apps packages tests`: passed.
- `node --check apps/admin/static/app.js`: passed.
- `git diff --check`: passed.
- Canonical Aiogram tests and Ruff remain pending because those dependencies are unavailable in this execution environment; no commit/push was created.


## Milestone 20: Phase 7.2 — Provider Configuration & Secret Boundary

- **Date:** 2026-09-15
- **Status:** Implemented in working tree; canonical Aiogram/Ruff verification pending
- **Continuation:** Continued directly from the user's Phase 7.1 directive without changing the existing authentication, tenant, financial, or fulfillment trust boundaries.

### Implementation

1. Added tenant-scoped supplier provider, credential-reference, product mapping, health-check, and payment-provider Admin APIs.
2. Restricted configuration mutations to ADMIN/OWNER while preserving STAFF+ safe operational reads.
3. Added Admin UI provider operations for supplier creation, enable/disable, health checks, routing mappings, and payment-provider settings.
4. Ensured read APIs expose only credential configured-status and never return secret references or resolved values.
5. Added recursive rejection of secret-like material in provider metadata, mapping metadata, and payment settings.
6. Wired `ProviderCredential.secret_ref` into actual runtime supplier execution through `ProviderRouter.build_provider_config()`.
7. Fixed fulfillment reconciliation to use the same runtime secret-resolution boundary as initial supplier execution.
8. Added payment-provider cache invalidation after configuration updates.
9. Hardened Telegram Stars Admin settings with an allowlist, XTR-only/whole-unit/native-invoice invariants, HTTPS Terms, bounded transaction scanning, and rejection of runtime API endpoint overrides.
10. Added ADR-014 documenting the provider configuration/secret boundary.

### Verification

- Phase 7.2 focused tests: **7/7 passed**.
- Phase 7.0 + 7.1 + 7.2 focused tests: **19/19 passed**.
- Full repository suite excluding only the two modules importing unavailable `aiogram`: **154/154 passed**.
- Alembic clean upgrade through `19c7d8e41f02 (head)`: passed on temporary SQLite; no Phase 7.2 migration was required.
- `python -m compileall -q apps packages scripts tests`: passed.
- `node --check apps/admin/static/app.js`: passed.
- `git diff --check`: passed.
- Canonical Aiogram tests and Ruff remain pending because those dependencies are unavailable in this execution environment; no commit/push was created.


## Milestone 21: Phase 7.3 — Members & RBAC

- **Date:** 2026-09-15
- **Status:** Implemented in working tree; canonical Aiogram/Ruff verification pending
- **Continuation:** Continued immediately after Phase 7.2 under the user's directive to keep progressing.

### Implementation

1. Added tenant-scoped member listing with search, role/active filtering, bounded pagination, and no Telegram numeric-ID exposure.
2. Added membership role, active-state, and permission mutation endpoints restricted to ADMIN/OWNER.
3. Enforced hierarchy: ADMIN cannot modify ADMIN/OWNER or grant those roles; only OWNER can perform those transitions.
4. Added last-active-owner protection and serialized membership mutations through a tenant row lock to prevent concurrent owner-removal races.
5. Kept role/active state live-authoritative: existing JWTs are rejected on the next request after membership deactivation.
6. Added permission identifier validation/deduplication and AuditLog records for membership changes.
7. Added Members UI with role/state filters, management dialog, deactivation confirmation, and client-side visibility matching server RBAC.
8. Added ADR-015 documenting privilege boundaries and the last-owner invariant.

### Verification

- Phase 7.3 focused tests: **5/5 passed**.
- Phase 7.0–7.3 focused tests: **24/24 passed**.
- Full repository suite excluding only the two modules importing unavailable `aiogram`: **159/159 passed**.
- No database migration required; migration head remains `19c7d8e41f02`.
- `python -m compileall -q apps packages scripts tests migrations`: passed.
- `node --check apps/admin/static/app.js`: passed.
- `git diff --check`: passed.
- Canonical Aiogram tests and Ruff remain pending because those dependencies are unavailable in this execution environment; no commit/push was created.


## Milestone 22: Phase 7.4 — Financial Resolution Center

- **Date:** 2026-09-15
- **Status:** Implemented in working tree; canonical Aiogram/Ruff verification pending

### Implementation

1. Added durable `FinancialResolutionCase` records linking operator workflow to payment intents, wallets, reversals, and reconciliation events without replacing ledger/provider evidence.
2. Added automatic case creation for Telegram Stars manual-review events and reversal-worker integrity failures.
3. Added migration `5a6e7f8b9c10` with backfill for unresolved production rows.
4. Added STAFF+ case queue/claim/release operations and ADMIN/OWNER resolution actions with optimistic version checks and row locking.
5. Added OWNER-only false-positive wallet reactivation with fail-closed checks for competing open cases/reversals.
6. Added Admin Financial Center UI and AuditLog coverage for all operator case mutations.
7. Added ADR-016 documenting the financial evidence/workflow boundary.

### Verification

- Phase 7.4 focused tests: **7/7 passed**.
- Full runnable suite excluding only the two modules importing unavailable `aiogram`: **166/166 passed**.
- Alembic clean upgrade/downgrade/upgrade through `5a6e7f8b9c10 (head)`: passed.
- Upgrade backfill test with an existing manual reversal + review event: passed and produced one linked CRITICAL case.
- `python -m compileall -q apps packages scripts tests migrations`: passed.
- `node --check apps/admin/static/app.js`: passed.
- `git diff --check`: passed.
- Canonical Aiogram tests and Ruff remain pending because those dependencies are unavailable in this execution environment; no commit/push was created.


## Milestone 23: Phase 7.5 — Analytics & Audit + Agent Traceability

- **Date:** 2026-09-15
- **Status:** Implemented in working tree; canonical Aiogram/Ruff verification pending

### Implementation

1. Added `packages.analytics.AdminAnalyticsService` as a read-only tenant aggregation boundary.
2. Added 7/30/90-day UTC analytics for order value/status, wallet top-ups/reversals/liability, fulfillment success, supplier performance/cost, operational exposure, and daily activity.
3. Enforced currency separation for all money metrics; analytics never sum USD/XTR or other currencies into one number.
4. Added tenant-scoped paginated Audit Explorer with action/resource/time filters, actor display, and defense-in-depth recursive secret-key redaction.
5. Added Admin UI Analytics/Audit views without introducing third-party chart dependencies.
7. Added migration `7b8c9d0e1f23` for time-window query indexes on orders, audit logs, payment intents, reversals, and fulfillment attempts.
7. Added ADR-017 and repository-wide agent traceability artifacts: `CURRENT_STATE.md`, `CHANGELOG_AGENT.md`, and `AGENT_HANDOFF.md`.
8. Added a mandatory documentation gate to `AGENTS.md` and the repository skill so code, verification state, and handoff documentation cannot silently drift.

### Verification

- Phase 7.5 focused tests: **3/3 passed**.
- Phase 7.4 + Admin + Phase 7.5 focused tests: **17/17 passed**.
- Full runnable suite excluding only the two modules importing unavailable `aiogram`: **169/169 passed**.
- Alembic clean upgrade through `7b8c9d0e1f23 (head)`: passed.
- Alembic `7b8c9d0e1f23 -> 5a6e7f8b9c10 -> 7b8c9d0e1f23`: passed.
- `python -m compileall -q apps packages scripts tests migrations`: passed.
- `node --check` for Admin and Mini App JavaScript: passed.
- `git diff --check`: passed.
- Canonical Aiogram tests and Ruff remain blocked because those dependencies are unavailable in this execution environment; no commit/push is created until the canonical gate passes.


## Milestone 24: Phase 7.9.1–7.9.2 — CI & PostgreSQL Concurrency Verification

- **Date:** 2026-09-15
- **Status:** Implemented in working tree; canonical external PostgreSQL/CI execution pending

### Implementation

1. Added GitHub Actions fast verification and PostgreSQL 17 concurrency jobs.
2. Added `Makefile`, `scripts/verify.sh`, and handoff-consistency validation as the shared human/agent release contract.
3. Added production-engine concurrency tests for wallet spend, checkout idempotency, settlement idempotency, durable fulfillment claiming, and wallet creation.
4. Added wallet-row locking for all balance mutations.
5. Added migration `8a9b0c1d2e34` to clean inherited payment-index drift and made `alembic check` mandatory.
6. Added ADR-018 and the verification/concurrency architecture guide.

### Verification

- Static checks available in this build container passed: compileall, Admin/Mini App JavaScript syntax, and `git diff --check`.
- Full `make verify` remains blocked locally because PostgreSQL/Docker and several declared dev dependencies are unavailable in this execution environment. It must run in GitHub Actions or the target Ubuntu environment before commit/push.


## Milestone 25: Phase 7.9.3–7.9.8 — Production Readiness & Phase 8 Entry

- **Date:** 2026-09-15
- **Status:** Working-tree implementation complete; canonical external execution pending before production cutover

### Implementation

1. Added one immutable multi-stage production image shared by migrations, API, worker, and bot runtime; production services run non-root/read-only with no-new-privileges and bounded tmpfs.
2. Added separate development Compose override and production-oriented healthchecks/trusted-proxy configuration.
3. Added structured JSON logging, correlation IDs, secret-pattern redaction, API liveness/readiness, worker/runtime Redis heartbeats, Prometheus-compatible metrics, and starter alert rules.
4. Added PostgreSQL backup/restore/restore-drill scripts with encryption policy, retention, and destructive-restore acknowledgement.
5. Added production fail-closed configuration validation, Redis-backed sensitive-route rate limits, payload limits, security headers, disabled production docs, secret scanning, Gitleaks, and dependency audit.
6. Added staging-only seeded HTTP E2E and guarded worker/Redis/PostgreSQL failure injection with recovery verification.
7. Added deployment, rollback, incident, security, DR, staging, and release runbooks.
8. Added `make release-gate`, which binds Git SHA and migration head to canonical verification, Compose validation, immutable image build, and optional staging/failure-injection evidence.
9. Added ADR-019 and the production-readiness architecture guide.

### Verification

- Local/static checks available in this environment passed: compileall, Admin/Mini App JavaScript syntax, `git diff --check`, production Settings fail-closed validation smoke, and repository secret scan.
- Dependency-limited fast regression on the final Phase 7.9.8 tree: **173 passed, 7 PostgreSQL tests deselected**, excluding the two direct-`aiogram` modules; a temporary external `aiosqlite` compatibility shim was used and is not committed.
- Canonical Ruff/Aiogram/PostgreSQL/Docker/staging/restore-drill execution is blocked in this build container by missing services/dependencies and must run in GitHub Actions or the target Ubuntu environment before production cutover.
- Migration head remains `8a9b0c1d2e34`; no schema change was required for Phase 7.9.3–7.9.8.


## Milestone 26: Phase 8.0 + 8.4 — Durable Bot Provisioning & Runtime Reconciliation

- **Date:** 2026-09-15
- **Status:** Working-tree implementation complete; canonical PostgreSQL/Ruff/Aiogram/release-gate execution pending externally

### Implementation

1. Added durable `BotProvisioningJob` state/lease/retry model with migration `9e1f2a3b4c56`.
2. Added tenant-idempotent request fingerprinting and Admin Bot Factory APIs/UI with STAFF read and ADMIN/OWNER mutation boundaries.
3. Enforced secret-reference-only provisioning and recursive secret/token-shaped config rejection; browser responses expose only configured/not-configured state.
4. Added worker-side runtime secret resolution and Telegram `getMe` verification. `telegram_bot_id` is the global identity/ownership authority.
5. Added fail-closed expected-username/cross-tenant checks, same-tenant convergence, transient retry/backoff, identity-race retry, and stale-lease crash recovery.
6. Added continuous bot-runtime reconciliation for create/update/disable/deleted/crashed polling state without process restart.
7. Added AuditLog lifecycle events, provisioning/fleet operational metrics, and Prometheus alerts.
8. Added ADR-020 and bot provisioning architecture documentation.

### Verification

- Phase 8 focused tests: **9/9 passed**.
- Fast runnable regression excluding PostgreSQL and the two direct-Aiogram modules: **182/182 passed**.
- SQLite Alembic clean upgrade/downgrade/re-upgrade through `9e1f2a3b4c56`: passed.
- SQLite `alembic check`: no new upgrade operations detected.
- Compileall, Admin JavaScript syntax, and `git diff --check`: passed.
- A canonical runtime-reconciliation test was added to the Aiogram-direct suite but cannot run in this build container because `aiogram` is unavailable. Real PostgreSQL/Ruff/Docker/staging release evidence remains externally pending.


## Milestone 27: Phase 8.1 + 8.5A — Versioned Templates, Per-Bot Branding & Trial Wizard

- **Date:** 2026-09-15
- **Status:** Working-tree implementation complete; first live Ubuntu/Telegram trial and canonical external release-gate evidence pending

### Implementation

1. Added immutable code-defined templates (`general-commerce`, `digital-goods`, `gift-cards`, `gaming-store`, `services`) with explicit version provenance and server-side config construction/validation.
2. Replaced raw-JSON normal provisioning UX with a guided Template → Telegram → Branding/Modules wizard while keeping legacy config API compatibility.
3. Added audited ADMIN/OWNER post-provision configuration updates that never expose or replace the bot credential reference.
4. Added signed `bot_id` JWT context after Telegram initData verification; storefront bootstrap uses it to apply bot-specific public branding over tenant defaults.
5. Added bot-specific Mini App display name/accent/logo/tagline and Telegram welcome/store/menu wording.
6. Added `ADMIN_PUBLIC_URL`, privileged `/admin`, `/whoami`, first-tenant OWNER/control-bot bootstrap script, ADR-021, architecture guide, and first-live-trial runbook.
7. No schema migration required; migration head remains `9e1f2a3b4c56`.

### Verification

- Phase 8.1 template/branding focused tests: **3/3 passed**.
- Fast runnable regression excluding PostgreSQL and the two direct-Aiogram modules: **186/186 passed** before final packaging checks.
- Canonical PostgreSQL/Ruff/Aiogram/Docker/staging/release-gate evidence remains external and must not be reported green until executed.

## Milestone 28: Phase 8.2A + 8.5B — Easy Start, Dynamic Local Vault & First-Run Web Installer

- **Date:** 2026-09-15
- **Status:** Working-tree implementation complete; live Ubuntu/Telegram trial and canonical external release-gate evidence pending

### Implementation

1. Added `scripts/easy_start.py` as the recommended self-hosted first-run entry point: generate missing local development secrets, URL-encode PostgreSQL credentials, start the Docker stack, wait for readiness, and print a one-time setup URL.
2. Added a shared Fernet-encrypted local `SecretStorage` vault with atomic writes, process file locking, restrictive permissions, environment-first compatibility, and runtime visibility across API/worker/bot-runtime.
3. Updated Admin Bot Factory provisioning to accept BotFather tokens directly, immediately convert them into opaque vault references, and bind direct-token replays to a non-persisted credential fingerprint. Legacy secret-reference requests remain compatible.
4. Added `/setup/` and `/api/v1/setup/*` with random setup-code authorization plus singleton database install locking.
5. First-run setup verifies the BotFather token with Telegram `getMe` before creating Tenant, OWNER Membership, and enabled control Bot; token material is never returned or persisted in PostgreSQL/AuditLog.
6. Added per-tenant Mini App/Admin public URLs with process environment fallback so the first-run UI can configure Cloudflare/public launch URLs without editing service environment.
7. Added migration `a1b2c3d4e5f6`, ADR-022, and `docs/runbooks/easy-start-web-setup.md`.

### Verification

- Easy Start focused tests: **4/4 passed**.
- Fast runnable regression excluding PostgreSQL and the two direct-Aiogram modules: **191/191 passed**.
- SQLite Alembic clean upgrade/downgrade/re-upgrade through `a1b2c3d4e5f6`: passed.
- SQLite `alembic check`: no new upgrade operations detected.
- Real PostgreSQL/Ruff/Aiogram/Docker live Easy Start/release-gate evidence remains externally pending.


## Milestone 29: Phase 8.7 — Bot Factory Release Candidate

- **Date:** 2026-09-15
- **Status:** Self-hosted Bot Factory feature-complete RC; canonical PostgreSQL/Ruff/Aiogram/release-gate execution pending externally

### Implementation

1. Live Ubuntu Easy Start trial completed successfully and the control Telegram bot responded.
2. Added identity-safe credential verification/rotation with durable status/version/timestamps while secret values remain only in SecretStorage.
3. Added Redis-backed expiring fleet observation and Admin verify/rotate/restart operations.
4. Added server-side per-tenant fleet capacity controls for total bots, enabled bots, and open provisioning jobs.
5. Added STABLE/CANARY release channels, runtime ownership filtering, audited channel changes, and an optional candidate-image rollout Compose overlay.
6. Added server-authoritative launch readiness for credential, runtime, HTTPS URLs, catalog, fulfillment routing, and payment funding readiness.
7. Added migration `b2c3d4e5f6a7`, ADR-023, fleet architecture documentation, and the RC runbook.

### Verification

- Phase 8 focused suites: **24/24 passed**.
- Fast runnable regression excluding PostgreSQL and the two direct-Aiogram modules: **198/198 passed**.
- SQLite upgrade to `b2c3d4e5f6a7` and `alembic check`: passed with no drift.
- Canonical PostgreSQL, Ruff, direct-Aiogram, Docker/staging, restore drill, and full release-gate evidence remain external requirements before production cutover.
