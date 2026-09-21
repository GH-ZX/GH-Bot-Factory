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

## Phase 9 — White-Label SaaS 🚧

### Phase 9.0 — Plans & Entitlements Foundation ✅ Implementation
- Persisted operator-owned SaaS plans and one current subscription record per tenant.
- Provider-agnostic billing customer/subscription references and lifecycle timestamps/status.
- Typed, fail-closed entitlement resolution with explicit tenant overrides.
- Existing self-hosted tenants preserve Phase 8 environment-configured limits until a subscription is assigned.
- Bot Factory total bots, enabled bots, and open provisioning jobs now enforce subscription-plan limits.
- Read-only tenant Admin Plan page shows source, plan/status, usage vs limits, and feature flags.
- Migration `c3d4e5f6a7b8`, ADR-024, architecture documentation, and focused regression coverage.

### Phase 9.1 — Platform Control Plane & Portable Hosting ✅ Implementation
- Installation-level `PLATFORM_ADMIN_TOKEN` boundary separate from tenant Bearer JWT/RBAC.
- Local-first `scripts/platformctl.py` for laptop hosting and later VPS-over-SSH operation.
- Plan catalog create/update/retire and audited tenant subscription assignment/clear operations.
- Retired plans are blocked for new assignment while existing subscribers continue safely until migrated.
- Durable provider-neutral `BillingEvent` idempotency/fingerprint boundary and normalized subscription convergence.
- Global `PlatformAuditLog` evidence separate from tenant AuditLog.
- Laptop -> VPS portable state export/import covering PostgreSQL + encrypted local secret vault, excluding rebuildable Redis and destination-owned `.env`.
- Migration `d4e5f6a7b8c9`, ADR-025/026, control-plane architecture, and portability runbook.

### Phase 9.2 — Billing Provider Adapter & Customer Portal ✅ Implementation
- Provider-agnostic billing adapter contract with Stripe as the first implementation; provider secrets stay environment-only.
- Separate `SaaSPlanPrice` catalog maps platform plans to provider price IDs without coupling entitlements to commercial pricing.
- Tenant OWNER/ADMIN checkout and hosted customer-portal handoff; browser redirects never mutate subscription authority directly.
- Signed Stripe webhook verification maps provider state into the Phase 9.1 durable `BillingEvent` convergence boundary.
- Laptop-first pull reconciliation via `platformctl billing-reconcile` provides convergence even when no public webhook ingress/tunnel exists.
- Deterministic past-due grace timestamps are persisted and exposed, while commercial feature disabling remains observe-only until Phase 9.3 policy gates are implemented.
- Migration `e5f6a7b8c9d0`, ADR-027, billing architecture documentation, local-first billing runbook, and regression coverage.

### Phase 9.3 — Product Entitlements ✅ Implementation
- Central commercial access states (`SELF_HOSTED`, `ACTIVE`, `GRACE`, `BLOCKED`) derived from normalized subscription status and persisted grace deadline.
- Self-hosted tenants remain fully functional without a subscription row; SaaS tenants fail closed after grace for commercial growth/premium mutations.
- Central feature registry and server-side gates for custom branding, canary rollout, and runtime restart controls.
- Bot provisioning/retry and bot enablement now require commercial access, while safe contraction/security actions remain available.
- Tenant Admin exposes configured vs effective feature flags and commercial-access reason; UI visibility is non-authoritative.
- ADR-028 and product-entitlement architecture documentation. No schema migration required; migration head remains `e5f6a7b8c9d0`.

### Phase 9.4 — SaaS Operations ✅ Implementation
- Periodic pull reconciliation runs inside the worker so laptop-hosted installations converge billing state without permanent public webhook ingress.
- Provider-managed subscriptions persist last successful sync timestamp/source plus bounded error evidence.
- Platform SaaS health summarizes self-hosted/subscribed tenants, commercial access state, stale provider synchronization, grace windows, and actionable alerts.
- Platform tenant-entitlement inspection is support-safe and does not mint tenant JWTs or impersonate users; inspection is recorded in `PlatformAuditLog`.
- `platformctl saas-health` and `platformctl tenant-entitlements` expose the same operational surface over localhost/SSH.
- Migration `f6a7b8c9d0e1` adds synchronization observability. ADR-029 records the no-impersonation/laptop-first operations boundary.

### Phase 10.0 — Provider Platform Core ✅ Implementation
- Provider connections are explicitly categorized as `NUMBER`, `ACCOUNT`, `GIFT`, `DIGITAL_PRODUCT`, `SERVICE`, or `OTHER` while preserving the legacy `provider_type` column as the adapter key.
- Adapter manifests publish supported categories, canonical capabilities, credential requirements, driver family, and safe operator metadata without embedding tenant secrets.
- Tenant Admin can discover adapter manifests and create category-compatible provider connections instead of hard-coding vendor names into commerce logic.
- Provider credential values may be submitted write-only and are stored in the encrypted local secret vault; SQL stores only deterministic secret references. Environment-only references remain supported.
- Connection tests validate adapter/category compatibility, required credentials, bounded timeout behavior, balance capability, and durable health evidence without returning raw secret material.
- Existing product mappings and resilient failover routing remain backward compatible; order calls gain a bounded per-provider timeout.
- Built-in Mock/Sandbox and example digital-code adapters now publish manifests suitable for development without live vendor accounts.
- Migration `a7b8c9d0e1f2`, ADR-030, and `docs/architecture/provider-platform.md` establish the extensibility contract.

### Phase 10.1 — Provider Categories & Canonical Operations ✅ Implementation
- Added canonical provider order states and delivery artifacts without leaking vendor-specific status strings into commerce/fulfillment logic.
- Added category-aware capabilities and explicit number/SMS contracts for services, countries, offers, reservation, activation status, SMS messages, cancellation, and completion.
- Added canonical offer/order DTOs for account, gift, and digital-service categories.
- Extended the Mock/Sandbox adapter with deterministic number/SMS lifecycle behavior for bot-template development without live supplier accounts.
- ADR-031 records the canonical-operation boundary.

### Phase 10.2 — Generic HTTP / OpenAPI Adapter ✅ Implementation
- Added a constrained declarative Generic HTTP adapter for Swagger/OpenAPI-shaped reseller APIs rather than an arbitrary scripting/code-generation engine.
- Supports bounded endpoint/auth/query/body/response selectors, canonical state/error mappings, dynamic capabilities, and dynamic credential requirements.
- Credentials resolve only through `SecretStorage`; persistable/raw responses are recursively redacted against resolved credentials.
- Redirects are disabled; private/reserved DNS/IP targets fail closed by default; production-like use requires HTTPS plus an installation-owned provider-host allowlist.
- Request timeout and response size are bounded. OpenAPI inspection accepts operator-supplied documents only and does not fetch/execute remote `$ref` targets.
- Custom reviewed Python adapters remain the escape hatch for APIs that cannot be represented safely.
- ADR-031 records the security model.

### Phase 10.3 — Multi-Provider Routing ✅ Implementation
- Added tenant-scoped `ProviderRoutingPolicy` with `PRIORITY`, `LOWEST_COST`, `AVAILABILITY`, `HEALTHIEST`, `WEIGHTED`, and `MANUAL` strategies.
- Variant-specific policy overrides product-default policy; legacy routing remains the compatibility fallback when no policy exists.
- Weighted routing is deterministic from routing/idempotency context rather than process-local randomness.
- Cross-provider failover is allowed only for explicitly retryable pre-order-safe failures; timeout/transport ambiguity never purchases from another supplier automatically.
- `LOWEST_COST` refuses mixed-currency comparison until an explicit FX normalization service exists.
- Migration `b8c9d0e1f2a3` and ADR-032 establish the routing contract.

### Phase 10.4 — Product Aggregation ✅ Implementation
- Added `ProviderOfferSnapshot` as the normalized latest observation per provider mapping: cost/currency, availability/stock, quantity bounds, observation/expiry timestamps, and bounded error evidence.
- Upstream observations remain separate from customer-facing Product/ProductVariant identity.
- Fresh unavailable observations remove mappings from eligibility; stale snapshots are advisory and do not become catalog authority.
- Live refresh is read-only and never creates an upstream order.
- Cost routing consumes fresh normalized observations and follows the single-currency safety rule.
- Migration `c9d0e1f2a3b4` and ADR-033 establish the observation model.

### Phase 10.5 — Provider Order Lifecycle ✅ Implementation
- Normalized asynchronous provider order responses into canonical states and delivery artifacts.
- Only `COMPLETED` may mark fulfillment complete; `CREATED`, `PENDING`, `PROCESSING`, and `WAITING_DELIVERY` remain active/non-terminal and `UNKNOWN` preserves ambiguity.
- Added bounded pull reconciliation in the worker so laptop/self-hosted installs converge supplier orders without public webhook ingress.
- Terminal supplier failures continue through existing durable fulfillment/refund invariants instead of bypassing accounting rules.
- Multi-item asynchronous supplier attempts that would require more than one upstream correlation fail safe to `UNKNOWN`; per-item correlation/saga is a future hardening requirement before broad production enablement.
- ADR-033 records the asynchronous convergence model.

### Phase 11 — Payments Platform ✅ Implementation

#### Phase 11.0 — Payment Core & Ledger Safety ✅
- Added tenant payment methods, immutable payment observations, assurance levels, amount/currency integrity gates, and database-enforced exactly-once settlement.
- Provider/browser/manual evidence cannot mutate wallet balances directly.

#### Phase 11.1 — Manual / Self-Custody Verification ✅
- Added manual proof/admin approval flow and installation-owned on-chain verifiers.
- TRON USDt and generic EVM token verification bind network, token contract, destination, exact integer amount, execution success, and finality.
- Canonical transaction identity prevents cross-tenant/case-variant replay.

#### Phase 11.2 — NOWPayments ✅
- Added direct payment creation, signed IPN verification, pull reconciliation, pay-currency binding, and exactly-once convergence.

#### Phase 11.3 — Triple-A ✅
- Added regulated-provider authenticated create/status polling. Unsupported webhook/refund surfaces remain fail-closed until authoritative contracts are implemented.

#### Phase 11.4 — Exchange Pay Adapters ✅
- Added Bybit Pay create/query + signed webhook + safe creation recovery.
- Added Binance Pay create/query polling integration. Unsupported callback/refund surfaces remain fail-closed.

#### Phase 11.5 — Payments Operations & Risk Hardening ✅
- Added generic payment-provider pull reconciliation for laptop/self-hosted convergence.
- Added creation-ambiguity handling, payment operations health, stale/unknown/manual-review/reversal visibility, and Financial Center integration.
- Added GoZaPay as an explicitly experimental gateway: idempotent invoices, HMAC-SHA256 webhook verification, polling, delayed settlement until provider `settled`, and explicit risk/parity acknowledgement.
- GoZaPay flexible/open-amount auto-credit remains intentionally disabled until Phase 12 introduces explicit multi-asset/FX semantics.
- Migration `d0e1f2a3b4c5` and ADR-034/035 establish the Phase 11 financial boundaries.

### Phase 12 — Commerce Economics / Pricing / Wallet / Reseller Engine ✅ Implementation

#### Phase 12.0 — Multi-Asset Wallet & FX Safety ✅
- Added high-precision asset wallets keyed by explicit asset/network identity and immutable idempotent asset-ledger transactions.
- Added explicit tenant FX policies (`PARITY` / `FIXED_RATE`) with acknowledgement and optional maximum auto-credit exposure; there is no implicit USDT/USDC = fiat assumption.
- Added idempotent wallet holds with available-balance enforcement plus capture/release lifecycle.

#### Phase 12.1 — Flexible Deposits & Optional Auto-Credit ✅
- Added durable open-amount `FlexibleDepositSession` for providers such as GoZaPay; open-amount deposits are no longer forced into a fixed-amount PaymentIntent.
- Auto-credit is configurable per payment method and snapshotted when the session is created.
- Asset-wallet auto-credit credits the verified asset/network amount exactly once. Fiat-wallet auto-credit requires an explicit matching FX policy; otherwise the session remains `SETTLED_REVIEW`.
- Pull reconciliation remains first-class so laptop/self-hosted deployments do not require public webhooks.

#### Phase 12.2 — Reseller Pricing & Profit Attribution ✅
- Added customer/reseller pricing tiers and global/category/product/variant rules with fixed, percentage, or mixed markup, minimum margin, and rounding increments.
- Checkout freezes one server-authoritative `CommercePriceQuote`; the displayed price, debited amount, and P&L sale amount use the same quote.
- Supplier pricing consumes fresh same-currency provider observations and fails closed for cross-currency pricing without explicit normalization.
- Fulfillment records actual upstream cost after canonical completion and computes realized gross profit when cost and sale currencies match.
- Corrected the provider mapping invariant: `product_id` references canonical `Product.id`; `product_variant_id` is only the optional refinement.

#### Phase 12.3 — Economics Operations ✅
- Added tenant Admin economics endpoints for pricing tiers/rules, FX policies, flexible-deposit visibility, order economics, and supplier balances.
- Added provider balance polling, durable low-balance/error evidence, and operator visibility without automatic provider disable/routing mutation.
- Added migration `e1f2a3b4c5d6`, ADR-036, and `docs/architecture/commerce-economics.md`.
- Dependency-limited runnable regression: **355/355 passed**, excluding PostgreSQL-only tests and the two direct-Aiogram modules unavailable in this environment.

### Phase 13 — Advanced Bot Templates & Factory Wizard ✅ Implementation
- Added vendor-neutral reseller, number/SMS, account, gift-card, digital-product, and hybrid templates over the shared core.
- Added a five-step Tenant Admin wizard: Template → Telegram → Branding → Business → Review.
- Added server-validated per-bot business profiles selecting compatible tenant providers, payment methods, routing strategy, default pricing tier, and optional flexible auto-credit permission.
- Enforced the saved profile in storefront payment exposure, pricing-tier resolution, flexible auto-credit, and fulfillment provider/category/routing selection.
- Preserved legacy behavior: bots without a business profile inherit existing product routing and tenant defaults; malformed persisted profiles fail closed.
- Added per-bot launch-readiness business checks, ADR-037, and `docs/architecture/advanced-bot-factory.md`. No schema migration was required.

### Release Qualification — next gate, not a new feature phase
- Core feature development is complete for the current product scope.
- Collect canonical PostgreSQL/Ruff/direct-Aiogram/Docker release evidence.
- Run staging E2E/failure injection and an encrypted backup/restore + laptop-to-VPS portability drill.
- Smoke-test only the real provider/payment adapters intended for launch using the smallest safe live transactions.
- New vendor adapters/templates may be added later without reopening core architecture.

### Phase 13 Repair Update — pending after 2026-09-16 review

See [the repair review](operations/REPAIR_REVIEW_2026-09-16.md) for code evidence and acceptance criteria.

- Enforce per-bot funding restrictions on legacy and new customer routes.
- Observe and escalate flexible-deposit reversals after credit.
- Keep failed portable imports offline until authoritative database/vault state is recovered and validated.
- Reject malformed auto-credit values and invalid signed bot contexts.
- Connect Mini App payment selection and open-amount deposit/status flows to the new APIs.
- Repair lint/environment gates, extend PostgreSQL races for new financial boundaries, and collect release/staging/restore evidence.
- Review baseline: 371 non-PostgreSQL tests passed, 8 deselected; canonical gate fails at 197 Ruff findings. Feature expansion remains deferred until repairs and release qualification are complete.

### Repair baseline — Admin access & financial hardening

User directive on 2026-09-16 supersedes the earlier roadmap-closure claim: repair the current baseline first, and wait for explicit authorization before new Phase 13 work.

- Repair browser Admin entry without weakening Telegram identity or tenant RBAC.
- Close existing funding policy and deposit-reversal gaps; connect the existing customer payment APIs.
- Keep failed restores offline and verify migrations/concurrency on PostgreSQL.
- Complete the canonical gate and browser regression checks before deployment.
- See [repair patch notes](operations/REPAIR_PATCH_NOTES_2026-09-16.md) for evidence and recommendations across earlier phases.
- Status: repairs complete and canonical gate green on 2026-09-16 (Ruff clean, 396 fast + 13 PostgreSQL tests, alembic clean at `f2a3b4c5d6e7`). Commit/push authorized on 2026-09-17; canonical gate rerun successfully. Remaining: operator-run immutable-image deployment and release-gate/staging/restore evidence. Existing host containers remain untouched under Law 4.

### Phase 13 — Advanced Bot Factory UX & Storefront Vertical Delivery ✅ Implementation

Delivered on 2026-09-17 upon user authorization:
- Telegram Mini App vertical adaptation (`NUMBER_SMS`, `ACCOUNT_SHOP`, `GIFT_CARDS`, `DIGITAL_PRODUCTS`, `MULTI_RESELLER`) for eyebrows, badges, theme styling, and search placeholders.
- Dynamic module navigation visibility enforcing bot `enabled_modules`.
- Order fulfillment delivery artifact exposure (`DeliveryArtifactResponse`) for authenticated customer order history and order detail.
- One-touch copy-to-clipboard for delivered codes, numbers, vouchers, and credentials with haptic and toast feedback.
- Admin console fleet cards displaying business profile chips, provider count, and routing strategy.
- LAN-accessible port 8010 binding via `${API_BIND_ADDRESS:-0.0.0.0}` while strictly preserving port 8000 for Portainer (Law 4).

### Local Staging Hardening & Split-Horizon HTTPS 🚧 Trusted TLS Pending

- Rotated the placeholder PostgreSQL credential after a successful isolated backup restore.
- Enabled Redis-backed rate limiting and narrowed API exposure to the server LAN address.
- Repaired backup/restore `.env` parsing and checksum portability.
- Selected `botfac.gh-store.me` as a dedicated temporary local endpoint so the existing React application at `gh-store.me` and existing `bot.gh-store.me` Zero Trust route are unaffected.
- Added and verified the UDM local DNS record plus Nginx Proxy Manager HTTP host; the local Admin URL is active at `http://botfac.gh-store.me/admin/`.
- Remaining operator step: attach a publicly trusted certificate to the active proxy host, validate local HTTPS, then switch `APP_ENV`, `ADMIN_PUBLIC_URL`, and `MINIAPP_PUBLIC_URL` to staging values.

### Phase 14 — Customer Marketplace, Tenant Handoff & Integration Catalog ✅ Delivered

The product and architecture plan is fully delivered and verified across Phase 14.0 through 14.6.

- **Phase 14.0 (Delivered):** ADR-039 boundary model, server-authoritative `TemplateGuidance` across all 11 bot templates, `BotTemplateResponse.guidance` API, and interactive Admin `? Help & Guidance` drawer with live search/filters.
- **Phase 14.1 (Delivered):** ADR-040 public sales boundary, public marketplace router (`/templates`, `/integrations`, `/estimate`, `/inquiries`), server-authoritative `QuoteEngine`, `CustomerInquiry` durable model with migration `a1b2c3d4e5f7`, and modern mobile-first web app at `botfac.gh-store.me/build/` with instant Telegram deep-link CTA.
- **Phase 14.2 (Delivered):** ADR-041 platform sales boundary, platform sales API router (`/api/v1/platform/sales/inquiries`, `/quotes`, `/quotes/{id}/accept`), `CommercialQuote` and `CommercialQuoteLine` models with migration `b2c3d4e5f6a8`, Admin dashboard Sales & Leads console with in-memory operator token gate, and `scripts/platformctl.py` CLI subcommands (`inquiries`, `quotes`, `quote-accept`).
- **Phase 14.3 (Delivered):** ADR-042 automated tenant handoff, `CustomerOnboardingService`, `CommercialQuote.tenant_id` foreign key via migration `c3d4e5f6a8b9`, tenant admin onboarding checklist API (`GET /api/v1/admin/onboarding/checklist`), and interactive Admin Overview progress widget.
- **Phase 14.4 (Delivered):** ADR-043 integration marketplace boundary, `IntegrationOfferingModel` and `TenantIntegrationEntitlement` models with migration `d4e5f6a8b9c1`, `IntegrationMarketplaceService`, platform grant/revoke endpoints, tenant admin discovery and write-only configuration API (`POST /admin/integrations/{key}/configure`), and interactive Admin API Marketplace modal.
- **Phase 14.5 (Delivered):** ADR-044 single-tenant isolation boundary, `DeploymentHandoff` model with migration `e5f6a8b9c1d2`, `DeploymentHandoffService` generating sanitized single-tenant export bundles (`bundle.json`, `manifest.json`, `docker-compose.standalone.yml`) with SHA-256 checksums, and managed runtime deactivation safety.
- **Phase 14.6 (Delivered):** ADR-045 commercial governance boundary, `CommercialGovernanceService` aggregating pipeline value, accepted revenue, and conversion metrics, platform sales KPI header grid, and release qualification test suite.
- Separate Factory Owner platform authority, Customer/Tenant Owner authority, customer staff, shoppers, and externally hosted customers.
- Explain all eleven current templates through one server-owned guide reused by an Admin **? Help** drawer and the public customer configurator.
- Add a modern public Build Your Bot journey for template, Bot/Mini App, product source, integrations, hosting, support, and contact choices.
- Start with human-reviewed inquiries and immutable quotes; keep automatic provisioning and charging out of the first release.
- Hand routine catalog, inventory, provider credential, mapping, pricing, payment, and staff management to the customer as tenant `OWNER`.
- Add a platform-owned integration/API catalog with explicit prices and tenant entitlements while keeping connections and credentials tenant-owned.
- Offer clearly separated managed hosting, dedicated portable deployment, and premium source-code licensing.
- Treat self-hosting/source delivery as a formal handoff after which the Factory Owner has no implicit technical control.
- Keep platform commercial pricing, tenant supplier costs, and shopper retail prices as three separate authorities.

Proposed delivery sequence: product contract/template guidance → public configurator and inquiries → owner sales/quote console → tenant onboarding → integration marketplace → deployment/source handoff → release qualification.


### Configurable configurator Telegram contact — 2026-09-19

Removed the hard-coded sales username from the configurator and the default settings. Both estimate and inquiry links use `OWNER_TELEGRAM_HANDLE` (username, @username, or Telegram profile URL). Empty configuration hides the chat action while preserving inquiry submission. This public sales setting is separate from Admin authentication and bot credentials. No schema or deployment change. Regression coverage includes non-default destinations, special characters, invalid profile URLs, and unconfigured inquiry submission. Canonical `make verify` passed: Ruff clean, 426 fast tests, 13 PostgreSQL tests, Alembic no drift, handoff/secret/JS/compile/dependency checks clean. A Node rendering smoke check passed for configured and unconfigured contact states. Verification ran outside the sandbox after sandboxed runs stalled. Live deployment and Telegram account reachability were not exercised.


### Reliable owner onboarding repair — 2026-09-19

Accepted quotes now create tenants only for unused slugs, require operator-confirmed numeric owner Telegram IDs, and serialize same-quote onboarding with PostgreSQL row locks. Retries cannot add/promote owners or reactivate disabled identities. Removed synthetic users/bots and fake login-code fallback. Purpose-bound, five-minute single-use owner setup grants permit tenant Admin access without a bot and recheck quote linkage, OWNER membership, active identity, and token version. Redis failures roll back with HTTP 503. Real bots use the existing verified provisioning wizard; template intent carries into the wizard/checklist. Admin/CLI require the owner ID and support renewed setup links. ADR-046 documents the boundary; migration head unchanged. Canonical `make verify` passed: 435 fast tests, 14 PostgreSQL tests including concurrent onboarding, Ruff clean, Alembic no drift, and handoff/secret/JS/compile/dependency checks clean. Admin browser regression passed (direct entry, failed login recovery, reload, logout, mobile layout). No deployment or automatic legacy-row repair; export hardening and first-live-sale/release qualification remain outstanding.


### Encrypted single-tenant portability & destination restore repair — 2026-09-19

Replaced plaintext single-tenant export with encrypted snapshots (`ghbf-tenant-encrypted-v2`) via Scrypt KDF + Fernet cipher. Added strict schema fingerprint binding and comprehensive table registry (`INCLUDED` and `EXCLUDED`), rejecting cross-tenant foreign key references. Secrets required by tenant records are resolved from source `SecretStorage` and encrypted inside the envelope; global user auth credentials (hashed passwords/emails) are stripped on export while incrementing `token_version`. Implemented safe offline destination restore script (`scripts/import_tenant_bundle.py`) importing into empty migrated databases only, populating destination `SecretStorage` with unique namespaced references, initializing install state, resetting environment URLs, and disabling bots by default until explicit activation and confirmed source shutdown. Added platform bundle download endpoint (`GET /api/v1/platform/sales/handoffs/{id}/bundle`), `--confirm-quiesced` flag to CLI and Admin UI passphrase dialog, volume `handoff_data:/var/lib/ghbf/handoffs`, and updated status chips. ADR-047 documents the boundary; migration head unchanged at `e5f6a8b9c1d2`. Canonical `make verify` passed: Ruff clean, 438 fast tests, 15 PostgreSQL tests (including isolated schema restore), Alembic no drift, handoff/secret/JS/compile/dependency checks clean.


## Phase 14 — Usability Simplification — 2026-09-20

Implemented five business categories with optional specialist presets, separate source and appearance choices, explained recommendations, collapsed advanced settings, shared simulated storefront/chat previews, actionable Home onboarding and launch checks, plain setup language, task-based navigation, consistent responsive/focus styling, and resumable non-secret drafts. Public appearance preferences are carried in inquiry notes; Admin drafts are scoped to store/user/bot and expire after 24 hours in session storage. Existing backend template keys, validation, and quote authority are unchanged.

Affected surfaces: `apps/admin/static/`, `apps/configurator/static/`; browser regression `tests/test_setup_ux_browser.cjs`; shared-script syntax added to `scripts/verify.sh`. No migration; head remains `e5f6a8b9c1d2`. Canonical `make verify` passed: 466 fast tests, 15 PostgreSQL tests, Ruff, migration upgrade/Alembic no drift, handoff/secret/JavaScript/compile/dependency checks clean. Existing Admin/Mini App browser regression and new setup UX browser regression passed. Desktop/mobile screenshots inspected; browser coverage includes drafts without tokens, tenant-separated draft restoration, failed submissions, simulated previews, collapsed choices, and mobile portrait/landscape layout. Final verification initially found trailing whitespace and a test synchronization issue; both were corrected, and the mobile wizard footer was improved before the passing rerun. No deployment or real Telegram/payment purchase was performed. Checklist: `docs/plans/usability-simplification.md`.


## Usability Deployment — 2026-09-20

Deployed UI commit `4e216ad` to the local installation. The normal image build hit Docker DNS failures during pip download; stopped that build and produced a static-only image from the existing `gh-bot-factory:local` base (previous running image `1962fd9cbb13c278c4d7740c67bb54d39ba6971a30d898445c5315ba813be5cb`). Copied only `apps/admin/static` and `apps/configurator/static`, preserving runtime dependencies. Tagged `gh-bot-factory:ux-4e216ad` and promoted it to `gh-bot-factory:local`; build manifest `sha256:9d68a6881b5d1c378a8dc4c0a9f6dfdbebd1fd893c72fd4f99c2938cc1beac28`. Recreated only API using Compose `--no-deps --no-build --wait`. Worker, bot runtime, PostgreSQL, Redis, proxies, and tunnels were not changed.

All five project services are healthy. `/health/ready`, `/admin/`, and `/build/` return 200 locally; served Admin/configurator JavaScript and shared assets match the verified source by SHA-256. Live Chromium checks at `http://10.70.5.5:8010` confirmed five business categories, a server-calculated gift-card estimate, and Admin sign-in without JavaScript errors. No real inquiry, bot creation, or purchase was performed. Schema remains `e5f6a8b9c1d2`. Prior canonical verification remains 466 fast + 15 PostgreSQL tests; deployment did not change application source.

The deployment host could not resolve `botfac.gh-store.me`; domain reachability from user devices is not verified. Direct LAN links are `http://10.70.5.5:8010/build/` and `http://10.70.5.5:8010/admin/`. No public-production release qualification is claimed.


## 2026-09-21 — Customer Builder Redesign

- Rebuilt `/build/` (`apps/configurator/static/`) with a cream/green visual system, illustrated storefront hero, clear delivery overview, four-step brief builder, interactive example, responsive cards and reduced-motion support. Static assets use `20260921_02`.
- Customer choices now cover 14 requested features (pricing, warranty, coupons, resellers, tickets, announcements, branding, catalog organization, users, history, purchase review, alerts, motion and custom emoji), supplier/payment connections and custom API requests, name/color/style, store/report languages, and customer-owned Supabase/PostgreSQL delivery preferences.
- Existing inquiry configuration plus bounded project notes carry the brief to the factory sales console. These are requested scope, not implemented capabilities or entitlements. No secret entry or provisioning was added.
- Public pricing is quote-on-review; no browser-authored prices. Existing server estimate snapshots and commercial APIs are unchanged. A one-time project preference is recorded for review; internal recurring estimates still require operator reconciliation before a final quote. See ADR-048.
- Tab drafts exclude contact, notes and custom API text. Error/retry states preserve entries; successful submission clears drafts and locks the submitted form. Telegram follow-up uses the inquiry reference without legacy estimated-price text.
- Verification: expanded browser regression passed, including catalog load retry, four steps, draft privacy/restoration, requested-feature persistence, maximum-length brief, failed/successful submission, existing Admin regression and 320/375px/landscape overflow checks. Desktop/mobile screenshots inspected. Canonical `make verify` passed on the current working tree: 470 fast tests, 15 PostgreSQL tests, Ruff, migration upgrade/Alembic no drift, handoff/secret/JavaScript/compile/dependency checks. The first run found an outdated page-title assertion, corrected before the passing rerun. Existing unrelated working-tree changes were present during verification; this milestone does not claim a separate clean-checkout gate or production qualification.
- Migration head unchanged: `e5f6a8b9c1d2`. No deployment performed; existing running site remains on its prior assets. Unrelated auth/Admin/setup/report work remains outside this milestone.


## Customer Builder Full Docker Redeployment — 2026-09-21

User authorized redeploying all factory Docker services to see the customer-page redesign. Built a clean Git archive of `48a11be9d6e4604de8c2e51a777c49e44555a5d2`; unrelated workspace edits were not included. Standard Docker build DNS failed; a build-only `network: host` override allowed the normal Dockerfile dependency install and build to finish. No host network, proxy, tunnel, or unrelated container settings were changed.

Image `gh-bot-factory:48a11be` was promoted to `gh-bot-factory:local`; manifest identity `sha256:9d11a29119f2cd8c7e8715b04380768706bba3472032010e6c3e0d214d017153`. Previous API/local image retained as `gh-bot-factory:before-builder-48a11be`. API, worker, bot runtime and migration job all use the same new image. PostgreSQL and Redis were also recreated as requested, retaining all named volumes. Migration job exited 0; schema remains `e5f6a8b9c1d2`.

All five long-running services healthy. `/health/live`, `/health/ready`, `/admin/` and `/build/` return HTTP 200. Served configurator HTML/CSS/JavaScript hashes match the committed source; asset version `20260921_02`. Live Chromium checked the four-step flow, five business groups, selected warranty/support features, review summary, mobile overflow and Admin loading with no page errors. No inquiry or purchase submitted. Container `pip check` passed. Build logs and live screenshot are under `/tmp/ghbf-deploy-48a11be/`.

Verified URL: `http://10.70.5.5:8010/build/`. Subdomain DNS lookup still fails from this host, so public-domain reachability is unverified. Port binding remains the existing `0.0.0.0:8010`. Prior source verification was 470 fast + 15 PostgreSQL tests on the workspace, not a fresh full gate inside this newly built image; no production-release qualification claimed. Documentation consistency and diff checks passed for this deployment record.


## 2026-09-21 — Customer Delivery Resume Handoff

Saved `docs/operations/RESUME_CUSTOMER_DELIVERY.md`: requested business model and deliverables, existing foundations versus unverified gaps, completed customer-builder changes/deployment, ordered next milestones with acceptance criteria, verification limits and dirty-worktree precautions. Immediate next milestone: review the live design and close request-to-approved-one-time-quote. The public feature choices are requests; backend pricing, full packaging, tenant/shopper polish and complete customer installation remain unfinished. Updated the delivery plan to mark only public-builder polish as delivered. Documentation only; no runtime or schema changes. Handoff consistency and whitespace checks passed; prior source/deployment verification is preserved, not rerun or relabeled.
