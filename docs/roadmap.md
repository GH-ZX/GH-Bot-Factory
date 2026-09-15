# Living Roadmap

This roadmap reflects the delivered sequence of the repository. Earlier planning numbers were superseded as fulfillment and security hardening expanded into dedicated milestones.

## Phase 1 — Foundation ✅
- Project structure
- Docker
- PostgreSQL
- Redis
- FastAPI
- CI and development standards

## Phase 2 — Commerce Core ✅
- Tenants and users
- Products and variants
- Orders and state machine
- Wallet ledger
- Authoritative checkout pricing

## Phase 3 — Telegram Runtime ✅
- Aiogram multi-bot runtime
- Bot registration
- Tenant routing
- FSM isolation
- Secret references and runtime safety

## Phase 4 — Provider Engine & Fulfillment ✅
- Provider protocol and credentials
- Product mapping and routing
- Failover
- Fulfillment attempts
- Durable jobs, retries, recovery, and reconciliation
- Database-enforced fulfillment refund idempotency

## Phase 5 — Payment Infrastructure ✅
- Payment provider abstraction
- Payment intents and transactions
- Webhook verification and deduplication
- Database-enforced settlement idempotency
- Payment reconciliation
- Telegram Mini App server authentication foundation

## Phase 5.1 — API Authentication & Authorization ✅
- Bearer JWT sessions
- AuthenticatedPrincipal
- RBAC and customer ownership
- Token-version session revocation
- Separate customer-session and gateway-webhook trust boundaries

## Phase 5.1.1 — JWT Signing Secret Hardening ✅ Implementation
- Mandatory `JWT_SECRET_KEY`
- No repository-known fallback secret
- Minimum signing-key length
- Generic malformed-token errors
- Focused secret leakage and key-rotation tests
- Canonical commit remains gated on the full dependency environment and Ruff

## Phase 6 — Telegram Mini App Storefront 🚧

### Phase 6.0 — Storefront Vertical Slice ✅ Implementation
- Static mobile-first Telegram Mini App served by FastAPI
- Telegram `initData` -> Bearer JWT login flow
- Public tenant bootstrap and wallet context
- Tenant-scoped catalog
- Ephemeral cart and Telegram MainButton integration
- Customer-owned order history
- Server-authoritative wallet checkout
- Database-backed checkout idempotency
- Client price/identity field rejection

### Phase 6.1 — Durable Checkout/Fulfillment Boundary ✅ Implementation
- Post-payment supplier execution is removed from the HTTP critical path
- Checkout stages `FulfillmentJobRecord` in the same transaction as the wallet debit/order commit
- A dedicated worker process polls durable `QUEUED` records and claims them atomically
- Startup recovery resumes abandoned queued/running work
- API container uses port 8010; port 8000 remains reserved by host infrastructure
- Order/refund/fulfillment idempotency invariants remain intact

### Phase 6.1.1 — Telegram Live Launch Integration ✅ Implementation
- Public HTTPS Mini App launch URL configuration (`MINIAPP_PUBLIC_URL`)
- Per-bot Mini App URL generation with authoritative internal `bot_id` routing metadata
- `/start` and `/menu` Web App buttons in private chats with legacy callback fallback
- Persistent Telegram menu button configured by the bot runtime
- Dedicated `apps.bot_runtime` process wired into Docker Compose
- Compose migration/health ordering for API, worker, and bot runtime
- Safe idempotent bot registration CLI storing secret references rather than tokens
- Cross-bot Telegram customer identity reuse within a tenant, including legacy `telegram_id` backfill
- Live launch runbook under `docs/runbooks/telegram-miniapp-live.md`

### Phase 6.2 — Payment Top-up UX ✅ Implementation
- Purpose-bound `WALLET_TOPUP` PaymentIntents without synthetic Orders
- Tenant-configured provider min/max/currency policies
- Idempotency binding across customer/provider/amount/currency
- HTTPS-only provider checkout URL handoff
- Verified webhook and provider reconciliation convergence
- Synchronous-success settlement support
- Database-enforced exactly-once wallet credit
- Mini App Add Funds sheet, pending-payment recovery, polling, and automatic wallet refresh
- Dedicated top-up refund/reversal flow intentionally deferred; legacy order refund path rejects top-ups

### Phase 6.2.1 — Telegram Stars Native Payments ✅ Implementation
- Production `telegram_stars` payment-provider adapter backed by the Telegram Bot API
- Native `createInvoiceLink` checkout with `XTR` and Mini App `Telegram.WebApp.openInvoice`
- Server-side `pre_checkout_query` validation against tenant, user, intent, amount, and currency
- `successful_payment` settlement converging on the existing exactly-once wallet ledger boundary
- Telegram transaction lookup for reconciliation
- Tenant-configured HTTPS Terms acceptance and `/terms` + `/paysupport` bot commands
- Bot-token reuse through `SecretStorage` references; no plaintext token is persisted in payment-provider configuration

### Phase 6.2.2 — Durable Stars Refund & Wallet Reversal ✅ Implementation
- Dedicated `WalletTopUpReversal` saga instead of reusing order-payment refund accounting
- Wallet funds are reserved/debited idempotently before an external Stars refund is attempted
- Database-enforced reversal/debit idempotency and one reversal per funded PaymentIntent
- Durable worker claims, crash recovery, retry backoff, reconciliation-required state, and fail-closed manual review
- Full-balance guard prevents refunding Stars after the customer has spent the corresponding internal wallet value
- Telegram `refundStarPayment` adapter with safe retry handling
- Tenant-scoped staff API for requesting and inspecting top-up reversals
- External platform chargeback detection remains a dedicated hardening follow-up

### Phase 6.2.3 — External Stars Chargeback Reconciliation ✅ Implementation
- Periodic authenticated `getStarTransactions` scanning for outbound user invoice transactions
- Durable provider-event audit/deduplication (`payment_reconciliation_events`)
- Correlation by Telegram charge ID, invoice payload, tenant, amount, and Telegram user identity
- Intentional local refunds are recognized and can close the provider-success/local-commit crash window without a second refund call
- Unrequested external reversals are mirrored into the wallet ledger exactly once
- Spent-value chargebacks freeze the affected wallet and enter operator `MANUAL_REVIEW` instead of allowing further checkout spend
- Staff reconciliation-event listing plus local manual resolution once value has been restored
- Migration `f02c4d8e5a63` adds the durable reconciliation audit boundary and provider-refund transaction uniqueness

### Phase 6.3 — Storefront Product UX ✅ Implementation
- Tenant-scoped text search across product title/description and active variant title/SKU
- Category + availability filtering with server-authoritative active-product visibility
- Deterministic offset pagination with bounded page size and total/next-page metadata
- Rich variant stock/SKU/delivery presentation with sold-out purchase guards
- Cart-safe variant cache so filtering/pagination never drops already selected cart lines
- Debounced Mini App search, load-more flow, skeleton loading, empty/error/offline states
- Stale-response protection for overlapping catalog requests

## Phase 7 — Admin Client 🚧

### Phase 7.0 — Tenant Operations Vertical Slice ✅ Implementation
- Separate Telegram-aware static admin client at `/admin/` reusing the existing verified `initData` → Bearer JWT flow
- Tenant operations bootstrap with product/order/fulfillment/reconciliation attention counters
- STAFF+ tenant-scoped read access; catalog mutation restricted to MANAGER/ADMIN/OWNER
- Category read/create/update
- Product search/list/create/update with active-state filtering and variant visibility
- Variant create/update with authoritative tenant ownership through the parent Product
- Tenant-scoped order operations list with customer summary and latest durable fulfillment-job status
- Tenant-scoped reconciliation review queue
- AuditLog records for catalog mutations
- No client-supplied tenant identity and no new authentication mechanism

### Phase 7.1 — Fulfillment Operations Center ✅ Implementation
- Tenant-scoped durable fulfillment job listing with dead-letter visibility and order search/filtering
- Attempt history with provider/external-order/error classification context
- Durable job failure classification plus manual requeue count/actor/timestamp metadata
- STAFF+ read access; high-risk requeue/reconciliation actions restricted to ADMIN/OWNER
- Fail-closed safe-requeue policy blocks UNKNOWN outcomes, existing upstream order IDs, terminal orders, permanent failures, and any order with a canonical fulfillment refund
- Atomic `DEAD_LETTER -> QUEUED` conditional update prevents concurrent duplicate operator retries
- Targeted single-order reconciliation instead of tenant-wide mutation from an operator click
- AuditLog records for every manual requeue and reconciliation run
- Migration `19c7d8e41f02` adds fulfillment operations metadata

### Phase 7.2 — Provider Configuration & Secret Boundary ✅ Implementation
- Tenant-scoped supplier-provider configuration and product/variant routing mappings
- STAFF+ safe read access; provider/routing/payment configuration mutations restricted to ADMIN/OWNER
- Supplier credential references stored separately from provider metadata; plaintext-looking secret material is rejected
- Runtime secret resolution shared by initial supplier execution and fulfillment reconciliation
- Supplier health checks use ephemeral resolved credentials without persisting or returning secret values
- Payment-provider configuration exposes only configured/not-configured secret status and invalidates cached adapters after updates
- Telegram Stars configuration is allowlisted and enforces XTR, native invoice checkout, whole-unit limits, HTTPS Terms, and bounded reconciliation scan settings
- Telegram Stars endpoint overrides are rejected to prevent bot-token exfiltration through operator-controlled configuration
- Provider, credential, routing, and payment-provider mutations are tenant-scoped and AuditLog-backed
- No database migration required; current head remains `19c7d8e41f02`

### Phase 7.3 — Members & RBAC ✅ Implementation
- Tenant-scoped member directory with search, role, and active-state filters
- Member administration restricted to ADMIN/OWNER
- ADMIN cannot mutate ADMIN/OWNER memberships and cannot grant ADMIN/OWNER
- OWNER can perform privileged role transitions while the last-active-owner invariant is enforced
- Tenant-row serialization protects concurrent owner changes from racing past the invariant
- Membership deactivation is enforced on the next protected request because authorization resolves the live Membership row
- Permission identifiers are normalized/validated and membership mutations are AuditLog-backed
- Admin UI hides member operations from non-admin roles and mirrors server-side management boundaries
- No database migration required; current head remains `19c7d8e41f02`

### Phase 7.4 — Financial Resolution Center ✅ Implementation
- Durable `FinancialResolutionCase` workflow layered over immutable provider/reversal evidence
- Automatic case creation from Stars reconciliation and reversal-worker manual-review transitions
- Upgrade backfill for pre-existing unresolved reversals/events via migration `5a6e7f8b9c10`
- STAFF+ tenant-scoped case visibility and assignment; resolution restricted to ADMIN/OWNER
- Optimistic case versions plus row locking prevent stale/concurrent operator decisions
- Safe resolution actions for external reversal debit, evidence-only acknowledgement, frozen-wallet closure, and OWNER-only false-positive unfreeze
- Wallet reactivation fails closed when another unresolved case/manual reversal exists
- AuditLog coverage for claim, release, and resolution actions
- Admin Financial Center with filters, wallet state, assignment, and server-authoritative available actions

### Phase 7.5 — Analytics & Audit ✅ Implementation
- Tenant-scoped 7/30/90-day operational analytics with UTC windows
- Currency-separated gross/net order value, wallet top-ups/reversals, and point-in-time wallet liability
- Fulfillment success/failure and supplier performance/cost metrics
- Operational exposure counters for dead letters, open financial cases, frozen wallets, and reconciliation reviews
- Daily order/funding activity without introducing a second accounting source of truth
- Tenant-scoped paginated Audit Explorer with action/resource/time filtering and actor display
- Defense-in-depth secret-like field redaction before audit details reach the browser
- Composite query indexes in migration `7b8c9d0e1f23`
- Repository-wide agent handoff/current-state documentation protocol

## Phase 7.9 — Production Readiness & Reliability ✅ Implementation Complete / Canonical Execution Pending

### Phase 7.9.1 — CI & Canonical Verification ✅ Implementation
- GitHub Actions fast quality gate on push/PR.
- Canonical `make verify` / `scripts/verify.sh` entry point.
- Ruff, compileall, handoff consistency, fast pytest, JS syntax, git diff, and pip integrity checks.
- CI JUnit/runtime/dependency snapshots for traceability.
- Dependabot for Python and GitHub Actions dependencies.
- Resolver lockfile remains a required follow-up for fully deterministic dependency installation.

### Phase 7.9.2 — PostgreSQL Concurrency Verification ✅ Implementation / Canonical Execution Pending
- PostgreSQL 17 CI service container.
- `alembic upgrade head` + `alembic check` as release gates.
- PostgreSQL concurrency tests for wallet overspend, checkout idempotency, payment settlement idempotency, fulfillment claiming, and wallet creation races.
- Wallet balance mutations serialized by authoritative row locks.
- Legacy payment index drift cleanup in migration `8a9b0c1d2e34`.

### Phase 7.9.3 — Production Containers & Deployment ✅ Implementation
- Immutable multi-stage application image reused by migrations, API, worker, and bot runtime.
- Non-root UID/GID, read-only runtime filesystem, tmpfs-only scratch space, no-new-privileges.
- Production-oriented base Compose plus explicit development override.
- Healthchecks and trusted-proxy configuration; API remains bound to localhost/private ingress by default.

### Phase 7.9.4 — Observability & Health ✅ Implementation
- Structured JSON logs with secret-pattern redaction and request correlation IDs.
- Separate liveness/readiness; PostgreSQL + Redis dependency checks.
- Worker/bot-runtime Redis heartbeats and Docker healthchecks.
- Prometheus endpoint/profile with HTTP, queue, financial-case, provider-health, and service-heartbeat metrics plus starter alert rules.

### Phase 7.9.5 — Backup / Restore / Disaster Recovery ✅ Implementation
- PostgreSQL custom-format backup script with retention and production encryption requirement.
- Destructive restore requires explicit production acknowledgement.
- Isolated restore-verification drill validates Alembic metadata and tenant readability.
- PostgreSQL documented as authoritative durable store; Redis remains rebuildable coordination state.

### Phase 7.9.6 — Security & Abuse Hardening ✅ Implementation
- Redis-backed production rate limiting for auth, money-moving, and Admin mutation surfaces.
- Request body limits, HSTS/CSP/nosniff/referrer/permissions/frame headers, production API-doc disablement, and proxy-trust guidance.
- Production-like settings fail closed on weak JWT, non-PostgreSQL DB, invalid Redis/rate-limit configuration, or insecure Mini App URL.
- Repository secret scan, Gitleaks workflow, dependency audit, non-root/read-only containers.

### Phase 7.9.7 — Staging Live E2E & Failure Injection ✅ Harness Implemented / External Execution Pending
- Staging-only seeded HTTP E2E covers readiness, bootstrap, catalog, wallet checkout, order visibility, and replay idempotency.
- Failure-injection harness kills/restarts worker and temporarily interrupts Redis/PostgreSQL, then requires health recovery and a second E2E pass.
- Guardrails refuse execution outside `APP_ENV=staging` and require explicit failure-injection acknowledgement.

### Phase 7.9.8 — Operational Runbooks & Production Release Gate ✅ Implementation / Canonical Execution Pending
- Deploy/rollback, observability/incident response, migration/provider/financial incidents, credential compromise, backup/restore/DR, staging, and security runbooks.
- `make release-gate` binds Git SHA + migration head to canonical verification, Compose validation, immutable Docker build, and optional staging/failure-injection evidence.
- Release evidence is written to `artifacts/release-evidence.json`.

> Phase 8 Bot Factory is now at a self-hosted release-candidate checkpoint. Production cutover remains blocked until the Phase 7.9 canonical release gate and staging recovery harness execute green in GitHub Actions or the target Ubuntu environment. Phase 9 is productization/SaaS scope, not required for a single-tenant self-hosted launch.

## Phase 8 — Bot Factory ✅ Self-Hosted Release Candidate

### Phase 8.0 + 8.4 — Durable Bot Provisioning & Runtime Reconciliation ✅ Implementation
- Tenant-scoped durable `BotProvisioningJob` with idempotency fingerprints, leases, retries, and crash recovery.
- ADMIN/OWNER provisioning requests submit secret references only; Telegram token material/config secret leakage is rejected.
- Worker resolves credentials only at runtime and verifies bot identity with Telegram `getMe`.
- `telegram_bot_id` is the global ownership authority; cross-tenant reuse fails closed.
- Admin fleet/job operations include inventory, retry/cancel, and enable/disable with AuditLog coverage.
- Bot runtime continuously converges enabled Bot rows into live polling tasks; create/update/disable/crash no longer requires process restart.
- Migration `9e1f2a3b4c56`, ADR-020, operational metrics, and provisioning alerts.
- Canonical PostgreSQL/Ruff/Aiogram/release-gate execution remains required externally before production cutover.

### Phase 8.1 — Templates & Branding ✅ Live Trial Passed
- Versioned server-owned templates for general commerce, digital goods, gift cards, gaming, and services.
- Per-bot branding for Mini App + Telegram menu/welcome surfaces, module selection, locale/currency, support handle/URL, HTTPS logo, and accent color.
- Template provenance persists as `_factory.template_key/template_version`; existing bots never silently change when defaults evolve.
- Mini App API context now carries a signed verified `bot_id`, allowing multiple differently branded bots per tenant without trusting browser-selected identity.
- Admin configuration edits preserve credential references and are audited.

### Phase 8.2A — Easy Start & Dynamic Local Credential Vault ✅ Live Trial Passed
- One-command self-hosted launcher generates missing development secrets, repairs `DATABASE_URL`, starts the full stack, and waits for readiness.
- Shared Fernet-encrypted local vault implements `SecretStorage` across API/worker/bot-runtime; environment references remain backward-compatible and take precedence.
- Vault-backed bot credentials become visible to runtime reconciliation without recreating containers.
- Admin Bot Factory accepts BotFather tokens directly and converts them to hidden vault references, removing the normal need to manage secret-reference environment variables.
- One-time installation state prevents first-owner/bootstrap replay.


### Phase 8.2A.1 — Easy Start transport resilience hotfix ✅

- Retry transient localhost TCP reset/refusal while Uvicorn is still starting instead of terminating the one-command launcher immediately.
- Live-trial observed; no schema change.


### Phase 8.2B — Credential Lifecycle ✅ Self-Hosted RC
- Durable credential verification/rotation status, version, last-verified/rotated timestamps, and normalized error type.
- Identity-safe BotFather token rotation verifies the immutable Telegram bot ID before replacing the encrypted vault value.
- Secret values remain outside PostgreSQL/API responses. External KMS/Vault adapters remain a production deployment extension, not a self-hosted RC blocker.

### Phase 8.3 — Fleet Operations ✅ Core RC
- Admin fleet view combines PostgreSQL desired state with expiring Redis runtime observation.
- Credential verify/rotate, safe manual runtime restart, enable/disable, and provisioning job controls are available without SSH.
- Runtime failures/backoff age out to OFFLINE instead of leaving stale RUNNING state.

### Phase 8.5A — Provisioning Wizard Core ✅ Live Trial Passed
- Template → Telegram secret reference/expected identity → branding/modules → durable launch workflow with server validation.
- Existing bots can be reconfigured through the same safe branding/template surface without credential exposure.
- First-tenant OWNER/control-bot bootstrap and Telegram `/admin` entry make the flow testable end-to-end.

### Phase 8.5B — First-Run Web Installer ✅ Live Trial Passed
- `/setup/` replaces the normal first-run CLI bootstrap with Store → Owner → Bot → Template/Public URL web setup.
- Telegram token is verified with `getMe`, stored only through the encrypted vault, and never returned by APIs.
- Optional public HTTPS base URL persists per tenant and drives Mini App/Admin Telegram launch URLs.

### Phase 8.5C — Launch Readiness Integration ✅ RC
- Server-authoritative final launch checklist for credential, observed runtime, HTTPS Mini App/Admin URLs, sellable catalog, fulfillment routing, and payment/funding readiness.
- Existing specialized Product/Provider/Payment pages remain the configuration surfaces; the wizard links readiness instead of duplicating those domain forms.

### Phase 8.6 — Limits & Safety ✅ RC
- Server-side maximum total bots, enabled bots, and open provisioning jobs per tenant.
- Existing Admin mutation rate limiting plus durable idempotency and worker leases prevent provisioning storms from bypassing UI controls.
- Enablement is blocked when the tenant enabled-bot cap is reached.

### Phase 8.7 — Bot Versioning & Safe Rollout ✅ RC
- Bot runtime revision and credential/template versions are visible operationally.
- Each bot is assigned to STABLE or CANARY. Runtime processes filter desired state by release channel.
- `docker-compose.rollout.yml` supports a candidate bot-runtime image owning only CANARY bots while the stable runtime owns STABLE bots.
- Moving a bot between channels is audited and bumps runtime revision for deterministic handoff/rollback.

## Phase 9 — White-Label SaaS
- Plans and billing
- Customer portal
- Limits and entitlements
- SaaS operations
