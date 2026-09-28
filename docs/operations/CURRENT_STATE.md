# GH-Bot-Factory Current State

> **Latest owner authorization — 2026-09-22:** Testing, verification, fixes, commit and push are now explicitly authorized. Earlier no-tests/no-push instructions below are historical. Fix MiniApp initial skeleton/bare-URL preview entry first, then use isolated verification environments; do not use a real customer.


> **Active hardening batch:** Ten targeted areas implemented; canonical verification and seven browser suites now pass; deployed baseline remains `work-20260922-hardening` pending verified-image rollout; see [hardening checklist](../plans/factory-hardening.md) and ADR-054. Project-only rollout completed; all five services report healthy. Testing and commit/push are authorized; see the latest verification checkpoint.

> **Prior completion checkpoint — 2026-09-22:** Steps 3/4/5 and MiniApp coding are implemented and deployed as `gh-bot-factory:work-20260922-completion` from uncommitted source. Source and live migration head: `c93eb541da62`. All five project services report healthy. Feature verification remains pending; no tests, real-tenant trials, commits or pushes until the owner requests them.

> **Resume here:** [Current checklist](../plans/factory-completion-next.md) and [customer delivery handoff](RESUME_CUSTOMER_DELIVERY.md). Earlier deployment and verification entries below are historical and do not qualify this batch.

> First stop for any human or coding agent resuming work. This file distinguishes implemented behavior from externally executed release evidence.

- **Last updated:** 2026-09-22
- **Current phase:** Customer product verification and MiniApp entry repair
- **Implementation status:** Customer-product/hardening source and MiniApp entry fix verified: 500 fast + 22 PostgreSQL, seven browser suites, dependency audit. Verified-image build/rollout and push pending.
- **Current migration head:** `c93eb541da62`
- **Current source migration head:** `c93eb541da62`, applied to the live project database.
- **Primary branch:** `main`
- **Previous milestone commit:** `c98fc0c` (`fix(ui): synchronize design system dialogs and bump static cache busters`)
- **Canonical repository:** `git@github.com:GH-ZX/GH-Bot-Factory.git`
- **Historical verification (web-factory source only, not current changes):** Final isolated-source make verify passed: 486 fast + 20 PostgreSQL; four browser suites and actual public owner login/sales/setup checks passed. No real-customer or production-release qualification claimed.
- **Deployment:** Uncommitted working-source image `gh-bot-factory:work-20260922-hardening` runs API, worker and bot runtime; all five project services report healthy. Migration `c93eb541da62`. Public Admin: https://factory.gh-store.me/admin/. Sample MiniApp: https://factory.gh-store.me/miniapp/?preview=1. This is not a qualified production release.
- **Local hardening:** On 2026-09-17 the placeholder PostgreSQL credential was rotated without exposing it, Redis-backed rate limiting was enabled, a restricted backup was restored successfully into an isolated database, and the API bind was narrowed to `10.70.5.5:8010`. UDM local DNS and Nginx Proxy Manager HTTP routing are active at `http://botfac.gh-store.me`; Admin, readiness, and all five Compose services are healthy. Trusted TLS, `APP_ENV=staging`, and the Mini App HTTPS URL remain pending. The existing `bot.gh-store.me` Zero Trust route remains untouched.

## Delivered Capabilities

1. Multi-tenant commerce, server-authoritative catalog/checkout, wallet ledger, RBAC, payments, Telegram Stars, reversal/reconciliation, and durable fulfillment.
2. Telegram storefront and tenant Admin console with fulfillment operations, providers, member RBAC, financial resolution, analytics, and audit.
3. Production-readiness layer: immutable containers, observability, health/heartbeats, rate limiting, backups/restore drills, staging failure injection, CI/release evidence, and PostgreSQL concurrency gates.
4. Bot Factory release candidate:
   - one-command Easy Start plus one-time web installer
   - encrypted shared local secret vault with Telegram `getMe` verification
   - durable idempotent bot provisioning and runtime reconciliation
   - versioned templates, per-bot branding, web provisioning wizard, `/admin`, `/whoami`
   - credential status/version/verified/rotated metadata without secret persistence
   - identity-safe BotFather token verification/rotation from Admin
   - Redis-backed expiring observed fleet state separated from PostgreSQL desired state
   - manual runtime restart intent through `runtime_revision`
   - authoritative tenant limits for total bots, enabled bots, and open provisioning jobs
   - STABLE/CANARY release channels with process-level ownership filtering
   - optional `docker-compose.rollout.yml` candidate-runtime overlay
5. SaaS productization foundation:
   - persisted `SaaSPlan` and one-current-`TenantSubscription` domain records
   - provider-agnostic billing references and subscription lifecycle fields
   - plan entitlements plus explicit tenant overrides with fail-closed validation
   - Bot Factory total/enabled/open-job limits resolved from the subscription plan
   - self-hosted fallback preserves Phase 8 environment-configured limits when no subscription exists
   - read-only tenant Admin Plan page shows subscription status, usage, limits, and feature flags
6. Platform/portability/billing operations:
   - independent installation-level platform token; tenant OWNER/ADMIN authority cannot mutate global commercial state
   - plan/subscription/billing-event control plane with `PlatformAuditLog`
   - provider-neutral billing adapter contract with Stripe as the first signed implementation
   - hosted checkout/customer portal plus pull reconciliation for laptop hosts without public ingress
   - central commercial access policy and periodic billing pull reconciliation
   - SaaS health/alerts and no-impersonation tenant entitlement inspection
   - quiesced portable PostgreSQL + encrypted local-vault export/import; Redis and `.env` intentionally excluded
7. Phase 10.0 Provider Platform Core:
   - canonical `NUMBER`, `ACCOUNT`, `GIFT`, `DIGITAL_PRODUCT`, `SERVICE`, and `OTHER` categories
   - safe adapter manifests with category-aware capabilities and declared credential requirements
   - write-only provider secret entry into `SecretStorage`; SQL/read APIs expose configured status only
   - durable connection health and bounded provider calls
8. Phase 10.1 Canonical Provider Operations:
   - vendor-neutral provider order states and delivery artifacts
   - explicit number/SMS service, country, offer, reservation, activation, SMS, cancel, and finish contracts
   - account/gift/digital-service canonical offer/order DTOs
   - category-aware sandbox behavior for development without live suppliers
9. Phase 10.2 Generic HTTP / OpenAPI Adapter:
   - constrained declarative endpoint/auth/request/response/status mappings rather than arbitrary code execution
   - dynamic capabilities and credential requirements derived from configured operations
   - OpenAPI/Swagger inspection for operator-supplied documents without remote `$ref` execution
   - no redirects, bounded timeout/response size, credential redaction, DNS/IP SSRF defenses, and production host allowlisting
10. Phase 10.3 Multi-Provider Routing:
   - tenant-scoped `PRIORITY`, `LOWEST_COST`, `AVAILABILITY`, `HEALTHIEST`, `WEIGHTED`, and `MANUAL` policies
   - deterministic weighted ordering from routing/idempotency context
   - cross-provider failover only for explicitly pre-order-safe failures
   - ambiguous timeout/network acceptance never auto-fails over to another supplier
   - mixed-currency cheapest routing fails closed until explicit FX normalization exists
11. Phase 10.4 Product Aggregation:
   - normalized provider-offer snapshots for cost, currency, availability, stock, quantity bounds, freshness, and error evidence
   - supplier observations remain separate from tenant/customer catalog identity
   - fresh unavailable observations affect eligibility; stale observations are advisory rather than authority
12. Phase 10.5 Provider Order Lifecycle:
   - only canonical `COMPLETED` may mark fulfillment complete
   - `CREATED`/`PENDING`/`PROCESSING`/`WAITING_DELIVERY` stay non-terminal and are reconciled later
   - `UNKNOWN` preserves ambiguity rather than causing duplicate purchase or premature refund
   - pull reconciliation is first-class so laptop/self-hosted operation does not require public webhooks
   - current multi-item asynchronous upstream execution fails safe to `UNKNOWN` when one durable attempt would need multiple upstream order correlations; per-item saga/correlation remains a future hardening step
13. Phase 11 Payments Platform:
   - canonical tenant payment methods plus provider configs with write-only encrypted credentials
   - immutable manual/on-chain/provider observations and assurance metadata
   - database-enforced exactly-once wallet credit with amount/currency/provider identity checks
   - self-custody TRON USDt and generic EVM token verification using installation-owned chain configuration
   - NOWPayments signed-IPN + polling integration
   - Triple-A regulated-provider create/poll integration with unsupported webhook/refund paths fail-closed
   - Bybit Pay create/query, signed webhook verification, and merchant-reference creation recovery
   - Binance Pay create/query polling integration with unsupported callback/refund paths fail-closed
   - GoZaPay experimental adapter with idempotent invoice creation, signed webhook verification, polling, delayed `settled` credit, and explicit risk/parity acknowledgement
   - generic provider reconciliation worker for laptop-first convergence without permanent public ingress
   - read-only payment operations health covering stale intents, creation ambiguity, manual review, reversal reconciliation, and financial cases
   - flexible/open-amount GoZaPay deposits deliberately do not auto-credit fiat wallets until multi-asset/FX semantics exist
14. Phase 12 Commerce Economics / Reseller Engine:
   - high-precision asset wallets and immutable idempotent asset-ledger entries
   - explicit PARITY/FIXED_RATE FX policies with no implicit stablecoin-to-fiat assumption
   - optional per-payment-method flexible-deposit auto-credit with creation-time policy snapshots
   - idempotent wallet holds and available-balance enforcement
   - reseller/VIP pricing tiers plus fixed/percentage/mixed markup rules and immutable checkout quotes
   - actual supplier-cost and gross-profit attribution after fulfillment
   - provider balance snapshots/low-balance alerts that never auto-disable routing
   - corrected canonical provider mapping identity (`Product.id` + optional `ProductVariant.id`)
15. Phase 13 Advanced Bot Factory:
   - vendor-neutral reseller, number/SMS, account, gift-card, digital-product, and hybrid bot templates
   - five-step Template/Telegram/Branding/Business/Review wizard
   - server-validated per-bot provider/payment/routing/pricing/auto-credit business profile
   - runtime enforcement across storefront payment exposure, pricing, flexible auto-credit, and fulfillment routing
16. Phase 13 Storefront Vertical Delivery & Artifact Exposure:
   - dynamic Telegram Mini App storefront adaptation (theme, eyebrow, catalog search placeholder) matching bot template and business profile
   - module navigation bounds enforcing `enabled_modules` configuration
   - delivered fulfillment artifact exposure (`kind`, `value`, `fields`) in customer order detail and list views
   - interactive clipboard copy with haptic and toast feedback for codes, numbers, and credentials
   - Admin fleet overview cards displaying vertical business profile tags, provider counts, and routing strategies
17. Agent-maintainability layer: current-state checkpoint, handoff protocol, changelog, ADRs, roadmap, prompt history, and machine consistency check.
18. Phase 14 product definition: public discovery/configuration, template guidance, manual sales/quotes, customer tenant ownership, priced integration entitlements, and managed versus self-hosted/source-delivery boundaries.
19. Phase 14.0 Template Guidance & Marketplace Boundaries:
   - ADR-039 defines the 4-tier actor boundaries (Factory Owner, Customer/Tenant Owner, Customer Staff, Shopper, Self-Hosted) and licensing terms.
   - All 11 versioned bot templates in `packages/factory/templates.py` possess server-authoritative structured `TemplateGuidance` (`product_source`, `what_you_can_sell`, `delivery_experience`, `operational_complexity`, `setup_requirements`, `example_business`, `limitations`, `supported_hosting`).
   - `BotTemplateResponse.guidance` exposed on `GET /api/v1/admin/bots/templates` for both internal Admin and future public configurators.
   - Admin Bot Wizard provides an interactive `? Help & Guidance` drawer with live search, categorization filters (`stored`, `provider_api`, `hybrid`), complexity indicators, and 1-click template selection.
   - Rebuilt Docker image `gh-bot-factory:local` and verified live.
20. Phase 14.1 Public Configurator & Messaging:
   - ADR-040 defines the public discovery boundary, server-authoritative quote calculations, and rate-limited inquiry capture.
   - Public marketplace router at `apps/api/v1/public_marketplace.py` exposing `/templates`, `/integrations`, `/estimate`, and `/inquiries`.
   - Server-authoritative `QuoteEngine` (`packages/marketplace/quotes.py`) calculating itemized setup and recurring fees across formats (`bot`, `miniapp`, `combo`), sources (`stored`, `provider_api`, `hybrid`), hosting models (`managed`, `dedicated`, `source_license`), and add-on integrations.
   - `CustomerInquiry` model (`packages/marketplace/models.py`) and migration `a1b2c3d4e5f7` recording lead details, configuration, quote snapshot, and IP hash.
   - Modern, mobile-first Web App in `apps/build/static/` mounted at `/build/` on `botfac.gh-store.me`, strictly preserving the apex domain `gh-store.me`.
   - Direct Telegram contact deep-link with URL-encoded inquiry summary.
   - Rebuilt Docker image `gh-bot-factory:local` and verified live on Compose services (`api`, `worker`, `bot-runtime`).
21. Phase 14.2 Platform Sales Console & Immutable Quotes:
   - ADR-041 records the platform sales boundary, commercial quote immutability, in-memory operator credential gate, and CLI/API operations.
   - `CommercialQuote` and `CommercialQuoteLine` models (`packages/marketplace/models.py`) with migration `b2c3d4e5f6a8`.
   - Platform sales router `apps/api/v1/platform_sales.py` for inquiry inspection, status progression, quote creation, and quote acceptance locking.
   - `PlatformAuditLog` tracking for inquiry status updates, quote creation, and quote acceptance.
   - Admin dashboard Sales & Leads console with in-memory operator token verification, lead inspection drawer, and formal quote generator dialog.
   - `scripts/platformctl.py` CLI subcommands (`inquiries`, `inquiry`, `inquiry-status`, `quotes`, `quote`, `quote-accept`) with auto-resolved LAN bind host.
   - Rebuilt Docker image `gh-bot-factory:local` and verified live on Compose services (`api`, `worker`, `bot-runtime`).
22. Phase 14.3 Customer Onboarding & Tenant Ownership:
   - ADR-042 defines the automated tenant handoff boundary, customer `Role.OWNER` assignment, and launch readiness checklist.
   - Added `tenant_id` foreign key and migration `c3d4e5f6a8b9_link_quote_to_tenant.py` linking accepted commercial quotes to created tenants.
   - Implemented `CustomerOnboardingService` (`packages/marketplace/onboarding.py`) providing idempotent tenant provisioning, user/membership assignment, template bot setup, single-use login grant, and audit logging.
   - Added platform endpoint `POST /api/v1/platform/sales/quotes/{quote_id}/onboard` and `platformctl quote-onboard` CLI subcommand.
   - Implemented tenant onboarding checklist API `GET /api/v1/admin/onboarding/checklist` (`apps/api/v1/admin_onboarding.py`) evaluating branding, catalog, payments, providers, and bot token status.
   - Built interactive onboarding progress widget in Admin Overview dashboard guiding the customer step-by-step to store launch readiness.
   - Rebuilt Docker image `gh-bot-factory:local` and verified live on Compose services (`api`, `worker`, `bot-runtime`).

23. Phase 14.4 Integration & API Marketplace:
   - ADR-043 defines the integration marketplace boundary, tenant entitlements, and write-only secret storage ingress.
   - `IntegrationOfferingModel` and `TenantIntegrationEntitlement` models (`packages/marketplace/models.py`) with migration `d4e5f6a8b9c1`.
   - Integration marketplace service (`packages/marketplace/integrations_service.py`) managing catalog discovery, entitlement granting/revoking, and tenant configuration status.
   - Platform control plane endpoints (`/api/v1/platform/sales/integrations`, `/tenants/{id}/integrations/{key}/grant`) with `PlatformAuditLog` audit logging.
   - Tenant Admin integration discovery API (`GET /api/v1/admin/integrations`) and write-only credential entry (`POST /api/v1/admin/integrations/{key}/configure`), failing closed (403) for unentitled tenants.
   - Interactive Integrations Marketplace modal in Admin Providers tab with status chips (`Active & Configured`, `Entitled`, `Upgrade Required`) and one-click credential configuration.
   - Rebuilt Docker image `gh-bot-factory:local` and verified live on Compose services (`api`, `worker`, `bot-runtime`).
24. Phase 14.5 Dedicated Deployment & Source License Handoff:
   - ADR-044 defines single-tenant isolation, digital license verification, managed runtime deactivation, and zero-backdoor operational boundaries.
   - `DeploymentHandoff` model (`packages/marketplace/models.py`) with migration `e5f6a8b9c1d2`.
   - `DeploymentHandoffService` (`packages/marketplace/handoff_service.py`) producing sanitized single-tenant export bundles (`bundle.json`, `manifest.json`, `docker-compose.standalone.yml`) with SHA-256 checksum verification.
   - Managed runtime deactivation terminating factory cluster polling before standalone VPS launch, eliminating Telegram HTTP 409 polling conflicts.
   - Platform API endpoints (`/api/v1/platform/sales/handoffs`, `/generate-bundle`, `/deactivate-managed`) and `scripts/platformctl.py` CLI subcommands (`handoffs`, `handoff-create`, `handoff-bundle`, `handoff-deactivate`).
   - Admin Sales & Leads dashboard tab displaying active deployment handoffs with 1-click bundle generation and deactivation controls.
   - Rebuilt Docker image `gh-bot-factory:local` and verified live on Compose services (`api`, `worker`, `bot-runtime`).
25. Phase 14.6 Commercial Governance & Release Qualification:
   - ADR-045 defines commercial governance boundaries, platform revenue tracking, and canonical qualification gates.
   - `CommercialGovernanceService` (`packages/marketplace/governance.py`) aggregating inquiry conversion rates, proposal pipeline values, accepted commercial revenue, standalone deployments, and top integrations without contaminating tenant shopper analytics.
   - Platform control plane API endpoint `GET /api/v1/platform/sales/governance/metrics` and CLI subcommand `scripts/platformctl.py sales-metrics`.
   - Admin Sales Console KPI header grid displaying real-time commercial indicators.
   - Release qualification test suite `tests/test_phase14_6_release_qualification.py` verifying metrics accuracy, tenant isolation, and zero secret leakage.
   - Rebuilt Docker image `gh-bot-factory:local` and verified live on Compose services (`api`, `worker`, `bot-runtime`).
26. Encrypted Single-Tenant Portability & Destination Restore:
   - ADR-047 defines the encrypted snapshot format (`ghbf-tenant-encrypted-v2`) via Scrypt KDF + Fernet cipher.
   - Schema fingerprint binding with explicit `INCLUDED` and `EXCLUDED` table registry; cross-tenant foreign keys fail closed.
   - Required secrets encapsulated inside the encrypted envelope; global user passwords/emails stripped while `token_version` is incremented.
   - Source quiescence required before export (`confirm_quiesced: bool = True` / `--confirm-quiesced`) with PostgreSQL `REPEATABLE READ, READ ONLY` transaction isolation.
   - Safe offline destination restore script (`scripts/import_tenant_bundle.py`) into empty migrated databases, secret vault injection, install state initialization, and environment URL resetting.
   - Platform API endpoint `GET /api/v1/platform/sales/handoffs/{handoff_id}/bundle` for authenticated operator download with SHA-256 integrity verification.
## Current Trust Boundaries
- Tenant/user identity always comes from `AuthenticatedPrincipal`; browser clients cannot choose authoritative tenant/user IDs.
- Money mutates only through ledger services and PostgreSQL serialization/idempotency controls.
- Provider/Telegram secret values exist only at runtime adapter boundaries. Write-only provider credential values may be accepted solely to place them into `SecretStorage`; SQL/read APIs expose only configured status/type, never the value or secret reference.
- Generic HTTP provider mappings are declarative configuration, not executable code. Redirects, unsafe/private destinations, unbounded bodies, and non-allowlisted production hosts fail closed.
- Raw/upstream provider payloads that may be persisted or surfaced are recursively redacted against resolved credential values.
- Supplier business logic is vendor-neutral: domain code consumes canonical categories, capabilities, order states, delivery artifacts, and offer observations rather than vendor names/status strings.
- Cross-provider failover is permitted only when the failure is proven retryable and pre-order-safe. Timeout/transport ambiguity after a possible upstream accept enters reconciliation instead of purchasing from a second supplier.
- Cost-based routing compares only authoritative fresh observations in one currency; mixed currency fails closed until explicit FX normalization exists.
- Provider-offer snapshots are observations, not customer catalog authority.
- Only canonical provider `COMPLETED` may mark an order fulfilled. Pending/unknown upstream states remain durable and converge through reconciliation.
- Bot provisioning and credential rotation treat Telegram `getMe().id` as identity authority.
- Credential rotation verifies the new token before replacing the old vault value; different Telegram identities fail closed.
- PostgreSQL Bot rows are desired state; Redis fleet state is non-authoritative, expiring observation only.
- Bot runtime ownership is filtered by release channel; mixed-version canary windows require backward-compatible schema/API behavior.
- A persisted tenant subscription makes its plan the authority for Bot Factory entitlements; malformed plan configuration fails closed. Retired plans remain valid for existing subscribers but cannot be newly assigned.
- Tenant Admin surfaces may read their own plan/usage but cannot assign plans, change billing references, or grant themselves entitlements; those writes require the separate installation-level platform control plane.
- Platform control-plane requests require `X-GHBF-Platform-Token`; tenant Bearer JWTs are not platform authority. Global mutations are recorded in `PlatformAuditLog`.
- Portable host migration treats PostgreSQL + encrypted local secret vault as authoritative portable state. Redis is rebuildable and `.env` is destination-owned.
- Billing checkout/portal URLs are navigation only; provider state becomes authoritative only after signed webhook verification or authenticated server-side reconciliation.
- Billing provider credentials/webhook secrets remain environment-only and are never persisted in SaaS models, audit metadata, or browser payloads.

## Active Documentation Sources

Read in order:

1. `docs/operations/CURRENT_STATE.md`
2. `docs/operations/AGENT_HANDOFF.md`
3. `AGENT_MAP.md`
4. `docs/roadmap.md`
5. `docs/operations/CHANGELOG_AGENT.md`
6. relevant ADRs under `docs/decisions/`
7. `docs/prompts/prompt_history.md`
8. `.agents/skills/gh-bot-factory-core/SKILL.md`
9. `docs/plans/phase-14-customer-marketplace.md` for the proposed next product phase

## Historical Packaging Verification (superseded by repair notes)

### Runnable/local evidence

- Phase 12 provider/commerce/economics focused regression: **33/33 passed**, including flexible deposits, mapping compatibility, provider balances, checkout profit attribution, and Admin economics coverage.
- Fast runnable regression excluding only the two direct-Aiogram modules unavailable here: **355/355 passed**.
- `python -m compileall -q apps packages tests migrations scripts`: passed on the final packaging tree.
- Admin/Mini App/Setup JavaScript syntax: passed on the final packaging tree.
- Repository secret scan on a temporary Git-backed copy: passed.
- `git diff --check` on the Phase 10.0 -> 10.5 staged patch: passed.
- SQLite migration upgrade through `e1f2a3b4c5d6`: passed.
- SQLite downgrade to `d0e1f2a3b4c5` and re-upgrade to `e1f2a3b4c5d6`: passed.
- SQLite `alembic check`: `No new upgrade operations detected.`

### Blocked/canonical evidence

- Real PostgreSQL `make verify-postgres`, including concurrency tests and migration drift: pending target Ubuntu/GitHub environment.
- Ruff: pending declared development environment.
- Direct-Aiogram modules, including runtime reconciliation/release-channel ownership: pending canonical environment.
- Immutable Docker execution, staging failure injection, restore drill, and complete `make release-gate`: pending target Ubuntu/GitHub environment.

## Documented Scope Limitations

1. The current `FulfillmentAttempt` still correlates one upstream external order identity per attempt. Multi-item asynchronous supplier orders fail safe to `UNKNOWN` until per-item upstream saga/correlation is implemented.
2. FX policy currently supports explicit operator-owned `PARITY` and `FIXED_RATE` contracts; there is no live market-rate/oracle adapter. Auto-credit therefore remains bounded by the tenant policy and optional maximum exposure.
3. Refund APIs remain enabled only for providers whose current authoritative refund/idempotency contracts are implemented and tested. Unsupported provider refunds stay fail-closed behind the durable financial-resolution/reversal workflow.
4. Provider balance monitoring is advisory/read-only by design; routing automation based on balance evidence is deferred until hysteresis/freshness policy is specified.

## Next Recommended Work
Complete the operator-owned trusted HTTPS proxy certificate cutover in [the local-domain reverse-proxy runbook](../runbooks/local-domain-reverse-proxy.md) to activate `APP_ENV=staging` and secure Mini App URLs. Run complete staging failure injection and restore drills before public launch.


### Configurable configurator Telegram contact — 2026-09-19

Removed the hard-coded sales username from the configurator and the default settings. Both estimate and inquiry links use `OWNER_TELEGRAM_HANDLE` (username, @username, or Telegram profile URL). Empty configuration hides the chat action while preserving inquiry submission. This public sales setting is separate from Admin authentication and bot credentials. No schema or deployment change. Regression coverage includes non-default destinations, special characters, invalid profile URLs, and unconfigured inquiry submission. Canonical `make verify` passed: Ruff clean, 426 fast tests, 13 PostgreSQL tests, Alembic no drift, handoff/secret/JS/compile/dependency checks clean. A Node rendering smoke check passed for configured and unconfigured contact states. Verification ran outside the sandbox after sandboxed runs stalled. Live deployment and Telegram account reachability were not exercised.


### Phase 14 — Reliable Owner Onboarding Repair

Accepted quotes now create tenants only for unused slugs, require operator-confirmed numeric owner Telegram IDs, and serialize same-quote onboarding with PostgreSQL row locks. Retries cannot add/promote owners or reactivate disabled identities. Removed synthetic users/bots and fake login-code fallback. Purpose-bound, five-minute single-use owner setup grants permit tenant Admin access without a bot and recheck quote linkage, OWNER membership, active identity, and token version. Redis failures roll back with HTTP 503. Real bots use the existing verified provisioning wizard; template intent carries into the wizard/checklist. Admin/CLI require the owner ID and support renewed setup links. ADR-046 documents the boundary; migration head unchanged. Canonical `make verify` passed: 435 fast tests, 14 PostgreSQL tests including concurrent onboarding, Ruff clean, Alembic no drift, and handoff/secret/JS/compile/dependency checks clean. Admin browser regression passed (direct entry, failed login recovery, reload, logout, mobile layout). No deployment or automatic legacy-row repair; export hardening and first-live-sale/release qualification remain outstanding.


### Encrypted Single-Tenant Portability & Destination Restore Repair — 2026-09-19

Replaced plaintext single-tenant export with encrypted snapshots (`ghbf-tenant-encrypted-v2`) via Scrypt KDF + Fernet cipher. Added strict schema fingerprint binding and comprehensive table registry (`INCLUDED` and `EXCLUDED`), rejecting cross-tenant foreign key references. Secrets required by tenant records are resolved from source `SecretStorage` and encrypted inside the envelope; global user auth credentials (hashed passwords/emails) are stripped on export while incrementing `token_version`. Implemented safe offline destination restore script (`scripts/import_tenant_bundle.py`) importing into empty migrated databases only, populating destination `SecretStorage` with unique namespaced references, initializing install state, resetting environment URLs, and disabling bots by default until explicit activation and confirmed source shutdown. Added platform bundle download endpoint (`GET /api/v1/platform/sales/handoffs/{id}/bundle`), `--confirm-quiesced` flag to CLI and Admin UI passphrase dialog, volume `handoff_data:/var/lib/ghbf/handoffs`, and updated status chips. ADR-047 documents the boundary; migration head unchanged at `e5f6a8b9c1d2`. Canonical `make verify` passed: Ruff clean, 438 fast tests, 15 PostgreSQL tests (including isolated schema restore), Alembic no drift, handoff/secret/JS/compile/dependency checks clean.


## Phase 14 — Usability Simplification — 2026-09-20

Implemented five business categories with optional specialist presets, separate source and appearance choices, explained recommendations, collapsed advanced settings, shared simulated storefront/chat previews, actionable Home onboarding and launch checks, plain setup language, task-based navigation, consistent responsive/focus styling, and resumable non-secret drafts. Public appearance preferences are carried in inquiry notes; Admin drafts are scoped to store/user/bot and expire after 24 hours in session storage. Existing backend template keys, validation, and quote authority are unchanged.

Affected surfaces: `apps/admin/static/`, `apps/configurator/static/`; browser regression `tests/test_setup_ux_browser.cjs`; shared-script syntax added to `scripts/verify.sh`. No migration; head remains `e5f6a8b9c1d2`. Canonical `make verify` passed: 466 fast tests, 15 PostgreSQL tests, Ruff, migration upgrade/Alembic no drift, handoff/secret/JavaScript/compile/dependency checks clean. Existing Admin/Mini App browser regression and new setup UX browser regression passed. Desktop/mobile screenshots inspected; browser coverage includes drafts without tokens, tenant-separated draft restoration, failed submissions, simulated previews, collapsed choices, and mobile portrait/landscape layout. Final verification initially found trailing whitespace and a test synchronization issue; both were corrected, and the mobile wizard footer was improved before the passing rerun. No deployment or real Telegram/payment purchase was performed. Checklist: `docs/plans/usability-simplification.md`.


## Usability Deployment — 2026-09-20

Deployed UI commit `4e216ad` to the local installation. The normal image build hit Docker DNS failures during pip download; stopped that build and produced a static-only image from the existing `gh-bot-factory:local` base (previous running image `1962fd9cbb13c278c4d7740c67bb54d39ba6971a30d898445c5315ba813be5cb`). Copied only `apps/admin/static` and `apps/configurator/static`, preserving runtime dependencies. Tagged `gh-bot-factory:ux-4e216ad` and promoted it to `gh-bot-factory:local`; build manifest `sha256:9d68a6881b5d1c378a8dc4c0a9f6dfdbebd1fd893c72fd4f99c2938cc1beac28`. Recreated only API using Compose `--no-deps --no-build --wait`. Worker, bot runtime, PostgreSQL, Redis, proxies, and tunnels were not changed.

All five project services are healthy. `/health/ready`, `/admin/`, and `/build/` return 200 locally; served Admin/configurator JavaScript and shared assets match the verified source by SHA-256. Live Chromium checks at `http://10.70.5.5:8010` confirmed five business categories, a server-calculated gift-card estimate, and Admin sign-in without JavaScript errors. No real inquiry, bot creation, or purchase was performed. Schema remains `e5f6a8b9c1d2`. Prior canonical verification remains 466 fast + 15 PostgreSQL tests; deployment did not change application source.

The deployment host could not resolve `botfac.gh-store.me`; domain reachability from user devices is not verified. Direct LAN links are `http://10.70.5.5:8010/build/` and `http://10.70.5.5:8010/admin/`. No public-production release qualification is claimed.


## Open Code Review Integration & Site Hardening — 2026-09-21

Installed `@alibaba-group/open-code-review` and established local delegation mode via `.agents/skills/open-code-review-delegate/SKILL.md`. Fixed security and accessibility items:
1. Hardened PBKDF2 iterations in `packages/core/auth.py` (`1_000 <= iterations <= 500_000`) against CPU exhaustion DoS.
2. Hardened `apps/api/v1/auth.py` with dummy password verification and multiple-candidate handling to prevent username enumeration timing side-channels.
3. Added active tab styles and accessibility attributes to Admin login.
4. Fast test suite passed: 470 passed (auth suite 24/24).


## Arabic Reporting & High-DPI Visual Diagrams — 2026-09-21

Equipped Antigravity with specialized skills `.agents/skills/arabic-document-reporting/` and `.agents/skills/diagram-generator/`. Generated 3 high-resolution 300 DPI system diagrams in `docs/reports/assets/`:
1. `diagram1_system_architecture.png`: 3-tier authority model and database isolation.
2. `diagram2_sales_onboarding_flow.png`: Sales pipeline from inquiry to 1-click onboarding.
3. `diagram3_order_fulfillment_flow.png`: Checkout, double-entry ledger debit, and automated self-healing refund guarantee.
Delivered executive Arabic Word report `docs/reports/GH_Bot_Factory_Full_Flows_AR.docx` (480 KB) with native RTL formatting and embedded diagrams, mirrored in `docs/reports/GH_Bot_Factory_Full_Flows_AR.md`. All generator scripts (`scripts/generate_arabic_flows_docx.py`, `scripts/generate_report_diagrams.py`) verified clean under Ruff.


## 2026-09-21 — Customer-Owned Delivery Completion Advisory

Requested primary product: one-time configured bot/Mini App delivery to customer Supabase + VPS, with paid integration additions and localized installation/admin reports. The inspected quote engine still adds recurring charges; encrypted tenant export is not a complete installable release. See [completion plan](../plans/customer-owned-bot-delivery.md) for ordered work, artifact contract and acceptance criteria. Implementation pending; no schema/runtime changes or fresh canonical/release verification claimed. Existing implementation checkpoint above is preserved as historical baseline.


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

## Phase 15 — Admin Identity and Tenant Operations (2026-09-21)

Steps 1 and 3 are implemented in source: matching cream/green Admin identity, structured project briefs and one-time customer-owned quotes; support/warranty review, coupons, durable announcements, catalog ordering/images/warranty terms, base-price editing and reseller/margin controls. Existing order/user/history/alert workflows are retained. Warranty approvals record manual decisions and never imply an automatic refund or replacement.

Migration: `0ce5d2c98e7f` (six tenant operations tables plus catalog ordering, quote scope and item sale terms). Only the disposable test database was migrated. Portable customer imports cancel pending announcements to prevent accidental sends on a destination. See ADR-049.

Workspace `make verify` passed with 478 fast + 18 PostgreSQL tests; migration upgrade and `alembic check` report no drift. New mocked browser workflows cover care decisions, coupon creation, draft/review/queue announcements, margin rules, project scope and one-time quote composition. Existing setup UX browser regression passed. Screenshots were visually inspected; reduced-motion mobile drawer transitions were corrected. Final isolated-source verification completed 2026-09-22: `make verify` passed with 474 fast + 19 PostgreSQL tests. The lower fast count excludes four pre-existing password-auth tests outside this milestone; the extra PostgreSQL test proves concurrent quote acceptance chooses exactly one sibling version. Admin operations and existing Admin/Mini App browser regressions passed against the isolated source. Logs: `/tmp/ghbf-operations-clean-verify.log`, `/tmp/ghbf-operations-clean-browser.log`, `/tmp/ghbf-admin-clean-browser.log`.

**Remaining:** Step 2 is the actual customer Supabase/VPS trial. Steps 4 (shopper/bilingual polish), 5 (automated package/report) and 6 (release qualification) are incomplete/deferred. Support/coupon APIs exist, but shopper-facing entry remains step 4. Campaigns support up to 10,000 recipients; the admin currently lists latest 100 campaigns and latest 200 coupons. New support-case/customer assignment selectors show the latest 100 members/orders. No live message, purchase, or new deployment was performed. Existing password/auth/setup/report work is preserved separately and must not be mistaken for part of this committed milestone.

Implementation commit `1d4fcec` was pushed to `origin/main` on 2026-09-22. Canonical isolated-source gate: 474 fast + 19 PostgreSQL; browser workflows passed. Live deployment and actual customer acceptance remain pending.

## Admin sign-in redesign — 2026-09-22

User confirms `factory.gh-store.me/build/` and `/admin/` are reachable. New sign-in design matches the existing cream/green Admin identity. Username/password and single-use Telegram admin token are supported; Account enables initial password setup and current-password-protected changes with session revocation. Password routes use auth rate limiting; ambiguous store membership requires an explicit verified store selection. Existing password work is now reviewed and included in this scope; unrelated setup/report work remains separate. ADR-050 documents the boundary. No new schema change; deployment must include the previous `0ce5d2c98e7f` operations migration. Focused tests: 28 auth tests and Admin/password/MiniApp browser regressions passed. Canonical isolated-source verification and live deployment are pending below.

Sign-in milestone verification: isolated-source canonical `make verify` passed (482 fast + 19 PostgreSQL), including Ruff, compilation, migration/no drift, JavaScript, secret/handoff and dependency checks. All three browser suites passed: Admin/password/MiniApp, tenant operations, and builder/setup UX. Desktop/mobile screenshots inspected. No real credentials or purchases used in these tests. Live Docker deployment is the next step.


## Sign-in deployment evidence — 2026-09-22

Source `cbe4591` is pushed and live at https://factory.gh-store.me/admin/. Clean-archive Docker image `gh-bot-factory:cbe4591` (also `local`) has identity `sha256:677476ee5269375dd2b9af467aec858254e0d9988364c46d05601b5834105e24`; revision label matches source. Previous image retained as `before-signin-cbe4591`. Restricted database backup: `backups/pre-signin-cbe4591/ghbf-20260921T214625Z.dump` with checksum. Migration `e5f6a8b9c1d2 → 0ce5d2c98e7f` completed before API/worker/bot-runtime rollout. PostgreSQL, Redis and Cloudflare configuration were not changed.

All five long-running services are healthy. Container dependency check passed. Public browser checks verified the new login identity, both tabs, 375px mobile layout, `/health/ready` HTTP 200, `/auth/account` rejecting unauthenticated access, and `/build/` rendering without page errors. Source suites passed: 482 fast + 19 PostgreSQL; all three browser regressions passed. Actual live credentials, purchases and announcements were not submitted. This is a development-installation deployment, not a production release-gate or real-customer acceptance claim.

Both the sign-in page and previously completed after-login Admin redesign are deployed. Users without a password can use the existing Telegram `/admin` one-time token, then choose **Account** to set a password. Existing-password changes require the current password and revoke all sessions. Username-less accounts need owner assistance; no default credentials were created. Steps 2 and 4–6 remain pending as previously agreed.

Logs/screenshots: `/tmp/ghbf-signin-verify.log`, `/tmp/ghbf-signin-build.log`, `/tmp/ghbf-signin-migrate.log`, `/tmp/ghbf-signin-live.log`, `/tmp/ghbf-signin-live-desktop.png`, `/tmp/ghbf-signin-live-mobile.png`.


## Web Factory Completion — 2026-09-22 (in progress)

Five-step checklist: docs/plans/web-factory-completion.md. Web-only setup and explicit installation-owner password sessions replace the owner-bot prerequisite; customer bots remain optional until verified provisioning. Shared factory theme/logo and setup redesign are implemented in the working tree. Accepted quote scope now carries through to customer configuration. ADR-051; source migration head a71c9e23b840. Live deployment is still cbe4591 / 0ce5d2c98e7f until verified rollout. Canonical gate and deployment pending; no live credentials changed yet. Real-customer tests and delivery packaging/reports remain deferred.


**Web factory source verification — 2026-09-22:** Final isolated-source canonical `make verify` passed: 486 fast + 20 PostgreSQL, Ruff, migration/no drift, secret/handoff/JavaScript/compilation and dependency checks. All four browser suites passed; desktop/mobile setup screenshots inspected. Theme propagation tests change one brand primitive and verify primary buttons/logo across build/Admin/setup. Password sessions open Sales without a second token. Initial isolated runs exposed an eager JWT dependency on legacy platform-token routes and test reliance on local environment; both corrected before the final passing run. Auth/setup validation responses omit secret-bearing input values. Log: `/tmp/ghbf-web-canonical.log`. Source migration a71c9e23b840; live rollout and requested account initialization are next. No real-customer/production qualification claimed.


## Web Factory Live Completion — 2026-09-22

All five current web-factory steps are complete and checked in docs/plans/web-factory-completion.md. Source `240a106` is pushed and deployed. `/setup/` is redesigned and now shows the initialized state; `ahmedghx` is configured with the supplied password (never recorded here). The factory has zero owner bots. Browser login at https://factory.gh-store.me/admin/ succeeds; Settings & operations → Sales & leads opens using the same password session without another token. Customer tenant roles do not grant factory/platform access.

Shared theme: apps/shared/static/theme.css. Builder, Admin/sign-in and setup share the brand primitive and derived colors. GH Store's logo mark is copied unchanged from sibling gh-store-dev. Customer bot branding remains independent. Customer brief → one-time quote acceptance → tenant configuration is covered through API/database integration tests; accepted scope preserves template/color/language and requested features without granting unreviewed entitlements.

Final isolated-source canonical verification: 486 fast + 20 PostgreSQL tests, migration/no drift, Ruff, secret/handoff/JavaScript/compilation/dependency checks. Four browser suites passed. Live browser checks passed for actual owner password login, factory checklist, sales access without platform-token entry, logout, mobile layout and locked setup; desktop/mobile screenshots inspected. An initial browser attempt could not click Sales inside a collapsed navigation section; opening Settings & operations fixed the walkthrough without application changes.

Deployment: clean-archive image gh-bot-factory:240a106, revision label 240a106, immutable image identity sha256:8be78618916743a97793107f83ed0e6282e29ea4144b44badbf170e419d1be9f. API/worker/bot-runtime use this image; all five services healthy. Local alias promoted; previous image retained as before-web-240a106. Migration 0ce5d2c98e7f → a71c9e23b840 applied after restricted backup backups/pre-web-factory/ghbf-20260921T235259Z.dump (checksum alongside). Owner initialized locally through the validated setup service in one transaction, reusing the explicitly requested orphan account; no Telegram Bot record created. No database/Redis/tunnel/other-host-container configuration was changed.

The initial Compose build reported success but its tag was unavailable at validation. Its fallback build was stopped before migration/rollout. A direct build from the verified archive was validated with its revision and dependency check, then deployed with building disabled. No unreviewed fallback image was deployed.

Evidence: /tmp/ghbf-web-canonical.log, /tmp/ghbf-web-build-direct.log, /tmp/ghbf-web-migrate.log, /tmp/ghbf-web-live-dashboard.png, /tmp/ghbf-web-live-setup.png. Development-installation acceptance only: real-customer Supabase/VPS testing, shopper/bilingual polish, automated Docker/migration/report packaging and production release qualification remain deferred/incomplete.


## 2026-09-22 — Customer product source implementation (unverified)

- Five tenant palettes share tokens between Admin previews and MiniApp. Shopper additions:
  English/Arabic UI/RTL, coupons, paginated personal orders and spending-wallet history,
  support conversations, sale-snapshot warranty requests, explicit cart access and bot shortcuts.
- Added `packages/delivery`, four tenant maintenance tables, and migration `b82da430c951`
  after `a71c9e23b840`. Admin Bot care & updates records issues, fix scope/version,
  temporary read-only diagnostic grants and owner-reported update approval/backup/rollback.
- Diagnostic capabilities are digest-only, expiring/revocable, header-only and audited;
  they never provide platform authority, credentials, customer records or remote command access.
- Platform handoff exporter can wrap encrypted state in a SQL/Compose/scripts/report ZIP.
  Image publication remains separate. Import now accepts the pristine installer seed,
  excludes diagnostic grants and cancels pending source update approvals on destination restore.
- Source checkboxes are updated in `docs/plans/customer-product-completion.md`; ADR-052
  records security, SQL-bootstrap and portability decisions. Existing exports need regeneration.
- **No tests, browser/static verification, migration execution, image build or deployment.**
  Owner requested coding without checks. Do not reuse old green counts. No push because main
  triggers CI tests. Source remains in the working tree; unrelated report/skill work is preserved.
- Live remains source `240a106`, migration `a71c9e23b840`. Next authorized batch must cover
  PostgreSQL/schema/import/concurrency and shopper/maintenance UI before any release claim.


## 2026-09-22 — Delivery/settings/releases/MiniApp completion (untested source)

Owner authorized simple Docker deployment and coding steps 3/4/5 plus MiniApp completion.
Tests, real-tenant trials, commits and pushes remain postponed until explicit instruction.

- Delivery center: platform-only versioned handoff plans, configuration/missing-requirement
  checklist, saved release/destination/report choices, package actions, history and
  explicitly operator-reported final acceptance/receipt references. Safe plan notes are
  included in the ZIP and localized report; no infrastructure passwords belong in this UI.
- Store settings: bounded tenant public profile, notice, FAQ, support/policy URLs and recipient
  guidance with optimistic concurrency. Existing bot/theme/language, warranty, pricing,
  reseller, coupons, announcements and financial operations are linked from one workspace.
- Support-to-release: affected version and owned support-case links; immutable tenant release
  catalog and release/issue associations; proposals validate the selected release and image.
  Linked issues become FIX_AVAILABLE, never automatically resolved or installed.
- MiniApp: product detail dialog; Help/FAQ/policies; four history types (spending ledger,
  asset ledger, funding, deposits); existing-payment review; keyboard/inert sheet handling;
  sample-only preview at `/miniapp/?preview=1`. Protocol payment statuses are no longer
  translated inside state comparisons. Normal signed Telegram authentication is unchanged.
- New migration `c93eb541da62` follows `b82da430c951`; release history is included in encrypted
  portability and old snapshots require regeneration. See ADR-053 and active checklist
  `docs/plans/factory-completion-next.md`.
- Initial Docker rollout of prior customer-product code completed: `work-20260922-product`,
  schema `b82da430c951`, services reported healthy. Final completion image deployed; see the deployment checkpoint below.
- No automated/manual functional tests or browser runs. Docker startup observation alone
  does not establish feature correctness or production readiness. No commit/push.


## 2026-09-22 — Completion batch deployed; verification deferred

- Built and deployed `gh-bot-factory:work-20260922-completion` from uncommitted working source.
- Applied PostgreSQL migration `b82da430c951 → c93eb541da62` successfully after the restricted
  backup `backups/product-20260922/before-completion.dump`. No restore drill was performed.
- Recreated only project API, worker and bot-runtime containers. Docker reports all five
  project services healthy; PostgreSQL/Redis containers and Cloudflare tunnels were unchanged.
- Delivery center, Store settings, Releases & fixes and the expanded MiniApp are in this image.
  Sample design preview: https://factory.gh-store.me/miniapp/?preview=1 (sample data only).
- Implementation and deployment boxes are checked in `docs/plans/factory-completion-next.md`.
  No functional tests, browser runs, verification gates, real-tenant trials, commits or pushes.
  Startup health is deployment evidence only; this source is not production-qualified.
- Build log: `/tmp/ghbf-completion-build.log`; Compose override: `/tmp/ghbf-completion-runtime.yml`.
  Use the override for this working image; do not assume the base Compose image tag includes it.
- Next: await the owner's authorization for the batched verification/fix pass, then commit/push
  after required gates pass. Real-customer delivery remains separately deferred.


## 2026-09-22 — Ten-area hardening source implementation

All ten targeted coding areas are checked in `docs/plans/factory-hardening.md`; ADR-054
records the authority, safety and evidence boundaries. Added guarded customer package
operations/backup scripts, scoped operational attention, pause-new-sales policy, session-based
MiniApp request recovery, installation evidence/sanitized reports, stricter maintenance steps,
API/session/rate-limit hardening and platform delivery progress. Existing commerce/admin
features are reused; no direct ledger mutation or remote support execution was introduced.

New routes: `/api/v1/admin/attention`, `/api/v1/admin/installation`,
`/api/v1/auth/account/revoke-sessions`, `/api/v1/platform/delivery/workspace/overview`.
Client modules: `attention.js`, `installation.js`, `pending-operations.js`.
Service: `packages/operations/attention.py`. Package helpers: `preflight.py`, `lifecycle.py`,
`backup.sh`, `update-approval.example.json`. Regenerate old customer packages.

No new migration (head `c93eb541da62`). Source review only, no tests or functional/browser
checks. No real tenant, commit or push. Final project-only Docker rollout pending; live
remains `work-20260922-completion` until the deployment checkpoint below is recorded.


## 2026-09-22 — Hardening batch deployed, functional verification deferred

Image `gh-bot-factory:work-20260922-hardening` built and deployed from uncommitted working
source. Only API, worker and bot-runtime were recreated. Docker reports all five project
services healthy. No migration was required or executed; schema remains `c93eb541da62`.
PostgreSQL/Redis containers, other host containers, ports and Cloudflare tunnels were unchanged.
Build log: `/tmp/ghbf-hardening-build.log`; working-image Compose override:
`/tmp/ghbf-hardening-runtime.yml`. Retain/use this override for the deployed working image.

Visible additions: Admin → Needs attention; Store settings → Pause new purchases and
Installation & backup evidence; Account → Sign out all account sessions; richer Delivery
center cards; MiniApp recovery panel when an unresolved money request exists. Generated
packages include the new guards/receipts/backup helper. Previously exported ZIPs must be regenerated.

All ten targeted coding upgrades and this deployment are checked in `docs/plans/factory-hardening.md`.
No automated tests, lint/syntax gates, browser checks, real-customer operations, commit or push.
Container startup is not feature acceptance or production qualification. The next authorized
verification batch must cover the new financial retry/locking behavior, tenant/RBAC isolation,
package lifecycle/import/backup/restore, UI/RTL and security boundaries before release claims.


## Customer product verification and MiniApp entry repair

Owner authorized isolated verification, fixes, commit and push. MiniApp skeleton and preview entry repaired; verification in progress. No real customer trial.


## 2026-09-22 — Customer product verification and MiniApp entry repair

Owner authorized testing, fixes, commit and push after successful gates. Earlier test deferrals
are historical. Real-customer trials remain deferred.

- MiniApp entry now redirects an unconfigured bare URL to the explicitly labelled preview.
  Bot-bearing links preserve signed-Telegram authentication and never silently become sample sessions.
- Render-blocking neutral skeleton hides the unthemed header, content, overlays and navigation;
  the selected theme is applied before revealing content. Errors use the current light identity
  and offer a preview link. Browser checks delayed the theme response and inspected screenshots.
- Fixed a PostgreSQL stale-identity-map defect: checkout refreshes the paused-sales policy under
  its shared row lock. A two-session regression proves a previously loaded tenant cannot bypass pause.
- Corrected restore rejection wording while preserving the empty/uninitialized destination guard.
- Verification now syntax-checks every Admin/MiniApp/shared JS module. Browser fixtures serve the
  real static dependency graph, including theme JSON and new modules.
- Added tests for scope/RBAC, version conflicts, bounded/revoked diagnostic grants, update evidence,
  alert acknowledgement, session revocation, secret-safe validation, pause/replay, package integrity,
  lifecycle rejection, fresh PostgreSQL SQL initialization and tenant restore, and browser money recovery.
- **Canonical `make verify` passed: 500 fast + 22 PostgreSQL**, no schema drift, Ruff, compilation,
  handoff/secret checks, JavaScript syntax, whitespace and dependency consistency. Two existing
  FastAPI deprecation warnings remain; they do not fail tests.
- **Seven browser suites passed:** Admin/login/MiniApp payments, operations, setup wizard, web setup,
  MiniApp entry/themes/RTL/skeleton, lost-response recovery, and hardening workspaces.
- Strict installed third-party dependency audit passed after updating the isolated development
  environment's pip to 26.2.1. `scripts/audit_dependencies.py` audits exact installed versions;
  it excludes only this first-party editable app, whose source is covered by the repository gates.
  pip-audit cannot query this private package from PyPI; no third-party finding is ignored.
- Source schema remains `c93eb541da62`. Fresh package PostgreSQL bootstrap/import passed on an
  isolated local database, not a real Supabase account or customer VPS. Remote provider acceptance,
  actual Telegram customer trial and staging/failure-injection qualification remain separate.
- Logs: `/tmp/ghbf-canonical-final.log`, `/tmp/ghbf-dependency-audit.log`, browser logs under
  `/tmp/ghbf-*-final-browser.log`, `/tmp/ghbf-miniapp-browser.log`. Release artifact build pending.

## 2026-09-28 — Bot wizard step 4 provider options repair

Repaired Step 4 provider option population in the Admin Bot Provisioning Wizard:
- Fixed `refreshPreferredProvider` in `apps/admin/static/app.js`: when no checkboxes are checked in `botProviderChoices` (the default state per instructions to allow all compatible connections), the "Preferred provider" dropdown (`#botPreferredProvider`) now lists all available compatible providers from `state.botWizardOptions.providers`, rather than rendering an empty dropdown with only "Select provider".
- When specific provider checkboxes are checked, the dropdown limits options to the checked candidates. If zero providers exist, it displays "No suppliers connected yet".
- Selecting a preferred provider in the dropdown now automatically marks its corresponding checkbox as checked and ensures `currentBotBusinessProfile` includes `preferred_provider_id` in `provider_ids` so backend validation (`preferred_provider_id must also be present in provider_ids`) succeeds.
- In `validateBotStep`, if `MANUAL` routing is selected without connected providers, the wizard displays a clear actionable error message rather than a generic prompt to choose from an empty list.
- In `apps/api/v1/admin_bots.py`, added `total_tenant_providers` count to `BotWizardOptionsResponse` and queried it in `get_bot_wizard_options`.
- In `loadBotWizardOptions`, added informative empty-state messaging when a tenant has connected suppliers that are disabled or incompatible with the selected template's category.
- Bumped Admin asset cache buster for `app.js` to `v=20260928_01` in `apps/admin/static/index.html`.
- Updated `tests/test_phase13_advanced_factory.py` with assertion on `total_tenant_providers`.

## 2026-09-28 — Clarification on Suppliers & Payments vs Bot Creation

Clarified relationship between store-wide "Suppliers & payments" and Bot Wizard Step 4 ("Connections"):
- Store-level integration authority: "Suppliers & payments" holds API credentials and tenant integration adapters.
- Bot-level routing scope: Step 4 is optional filtering. A bot can be created before suppliers are added and will dynamically route through any compatible store suppliers added later.
- Manual routing dependency: Step 4 "Manual selection" requires at least one compatible supplier configured in "Suppliers & payments".

## 2026-09-28 — Bot runtime router re-attachment defect repair (@mrandroid_robot)

Repaired multi-bot and restart defect in Telegram Bot Runtime (`packages/telegram/routers/__init__.py` and `packages/telegram/runtime.py`):
- Root cause: In aiogram 3, attaching a sub-router sets its `parent_router`. When a bot configuration changed or a bot was restarted, `get_root_router()` attempted to re-include module-level singleton routers into a new root router, throwing `RuntimeError: Router is already attached`.
- Fix: Implemented `copy_router(source)` in `packages/telegram/routers/__init__.py` to clone observers, handlers, and middlewares into a fresh unattached router instance per dispatcher.
- Enhanced `_stop_instance` in `packages/telegram/runtime.py` to gracefully stop polling before task cancellation.
- Added `greenlet>=3.0` and `sqlalchemy[asyncio]>=2.0` to `pyproject.toml` for container build dependencies.
- Added regression test `test_create_dispatcher_can_be_called_repeatedly` in `tests/test_telegram_runtime_and_middleware.py`.
- Rebuilt Docker images; verified `@mrandroid_robot` successfully converged to live polling.

## 2026-09-28 — MiniApp Help Consolidation into Settings Page

Refined MiniApp navigation and page architecture following `gh-store-tele` patterns:
- Removed standalone "Help" button from the bottom navigation bar (`apps/miniapp/static/index.html`).
- Renamed the 3rd bottom navigation button to "Settings" (`⚙ Settings`), consolidating navigation to 3 clean tabs: Shop, Orders, Settings.
- Nested the Help & Support sections (store description, policy links, FAQs, and CustomerCare support ticket system) directly inside the Settings view (`#settingsView`, aliasing `#accountView`).
- Updated `apps/miniapp/static/styles.css` to set `.bottom-nav` grid template to `repeat(3, 1fr)` and styled `.settings-help-panel`.
- Added Arabic translation `"Settings": "الإعدادات"` in `apps/miniapp/static/locale.js`.
- Updated `apps/miniapp/static/app.js` and `apps/miniapp/static/customer-care.js` to route `help` and `account` targets to `settings` with smooth-scroll to `#supportSection`.
- Updated `tests/test_miniapp_entry_browser.cjs` and `tests/test_admin_browser.cjs` to target `[data-target="settings"]`. All 7 browser test suites pass.
- Bumped MiniApp asset cache busters to `v=20260928_01`.

## 2026-09-28 — MiniApp Homepage UI/UX Cleanup & Header Realignment

Executed MiniApp homepage and UI/UX refinements:
- Header docked at the absolute top of the app shell: removed the floating `.language-bar` from above the header so `<header class="topbar glass-panel">` sits directly at the top.
- Removed internal business types from the header: deleted `<span id="storeEyebrow">` so technical template types ("✨ HYBRID DIGITAL STORE", "📱 VIRTUAL NUMBERS & SMS", etc.) are never shown to end-customers.
- Removed internal vertical badges from the homepage: deleted `<div id="verticalBadge">` from the hero card so "⭐ Unified Products & Services" and internal vertical tags are completely hidden.
- Relocated Language Selector into Settings: moved the interface language dropdown (`#storeLanguage`) into `#settingsView` under a dedicated Preferences section.
- Cleaned homepage shortcuts: removed the `#shopSupport` ("Help & support") card from the homepage `.shop-shortcuts` grid, leaving a clean, focused "Add funds" action.
- Updated `apps/miniapp/static/styles.css` `.shop-shortcuts` layout to 1 column.
- Updated `tests/test_miniapp_entry_browser.cjs`; all 7 browser suites passed.
- Rebuilt Docker image and redeployed runtime containers.

## 2026-09-28 — Dedicated Wallet Page & Full ghstoretele Integration

Architected and integrated a dedicated Wallet page inspired by `gh-store-tele`:
- Removed all wallet components (balance cards, Add funds button, wallet grid, activity history, and filters) from Settings (`#settingsView`). Settings is now purely focused on Profile, Preferences, and Help/Support.
- Created a dedicated Wallet view (`#walletView`, `data-view="wallet"`):
  - Hero balance banner with eyebrow, available balance label, large `$0.00` balance headline (`#walletHeroBalance`), and spending status ("Ready for purchases").
  - Wallets grid (`#walletGrid`) displaying all currency and asset balances.
  - Embedded Recharge Action card with a prominent "Add funds now" trigger (`#walletRechargeButton`) that directly opens the topup form.
  - Wallet transaction history section with activity filter (`#accountHistoryKind`) and pagination.
- Updated the bottom navigation bar (`<nav class="bottom-nav">`) to 4 clean tabs matching `gh-store-tele`: Shop (`⌂`), Orders (`◫`), Wallet (`▤`), and Settings (`⚙`).
- Updated `apps/miniapp/static/styles.css` with 4-column grid layout for `.bottom-nav` and custom styles for `.wallet-hero`, `.wallet-hero-balance`, `.wallet-hero-status`, and `.recharge-action-card`.
- Added Arabic translation `"Wallet": "المحفظة"` and related strings in `apps/miniapp/static/locale.js`.
- Updated `apps/miniapp/static/app.js`: updated `switchView` to handle `wallet`, updated `renderBootstrap` to calculate `walletHeroBalance`, and linked `#shopRecharge` to navigate to the Wallet view.
- Updated browser test suite (`tests/test_admin_browser.cjs`). All 7 browser test suites passed cleanly.
- Rebuilt Docker image and redeployed runtime containers.

## 2026-09-28 — Confirmation of Factory-Wide Architecture for Future Bots

Confirmed factory architecture and universality of changes:
- Shared MiniApp Frontend: All bots share `apps/miniapp/static/` served by the central API. Any bot opens `{miniapp_url}?bot_id={id}` and loads the 4-tab layout (Shop, Orders, Wallet, Settings), top-docked header, and consolidated settings help automatically.
- Shared Telegram Runtime: `packages/telegram/routers/__init__.py` clones routers per dispatcher, allowing unlimited concurrent bots and restarts.
- Shared Admin Creation Wizard: The Step 4 provider fix and onboarding logic are built into `apps/admin/static/` and `apps/api/v1/admin_bots.py`, applying to every new bot.

## 2026-09-28 — Session Handover Checkpoint

Final session check completed:
- All todo items completed and closed.
- All 7 Playwright browser test suites verified and passing.
- Static JS syntax verified across all modules (0 errors).
- Docker image rebuilt and deployed; all services healthy; `@mrandroid_robot` active and live polling.
- Repository clean and ready for continuation.

## 2026-09-28 — Wallet Page Fine-Tuning & Brand Cleanup

Executed focused fine-tuning of the MiniApp Wallet page:
- Removed `GH / ` prefix from `apps/miniapp/static/index.html` (eyebrow changed to clean `WALLET`).
- Integrated primary `#walletRechargeButton` ("Add funds") directly inside `.wallet-hero-inner` with sleek rounded pill styling, eliminating the redundant bottom `.recharge-action-card`.
- Added `.status-indicator-dot` with glowing indicator next to "Ready for purchases".
- Streamlined the Transactions filter dropdown with compact `.wallet-filter-bar` and `.wallet-filter-select`.
- Updated `apps/miniapp/static/locale.js` with `"BALANCES": "الأرصدة"` and `"TRANSACTIONS": "المعاملات"`.
- Updated `apps/miniapp/static/app.js` to toggle `#walletRechargeButton` visibility when top-up options are not available.
- Bumped asset cache busters to `v=20260928_02`.
- Verified browser test suite (`test_miniapp_entry_browser.cjs` and `test_admin_browser.cjs`) passing 100%.

## 2026-09-28 — Wholesale Suppliers & Payment Connectors Live Integration

Configured, encrypted, and live-verified all wholesale supplier providers, payment methods, and automated catalog imports for the factory bot (`@mrandroid_robot`):
- **Live Wholesale Suppliers:** G2Bulk (Gaming Top-Ups, live balance `$8.71 USD`) and VenteBot (Accounts & AI, live balance `$34.38 USD`) connected, encrypted via `SecretStorage`, health verified `HEALTHY`.
- **Payment Methods:** Telegram Stars (`XTR`), Sham Cash (`0968098330`), Syriatel Cash (`0968098330`), USDT TRC-20 (`TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE`) configured and active.
- **Bot Routing Profile:** Bound providers and payment methods to `@mrandroid_robot`'s `_business` profile with `HEALTHIEST` routing strategy.
- **Live Catalog Imported:** 8 products across "AI & Subscriptions" and "Gaming Top-Ups" imported with server-authoritative markup calculations.

## 2026-09-28 — Minimal Product Cards & Rich Dedicated Product Page

Overhauled product presentation and storefront shopping experience across all factory bot templates:
- **Tenant API Key Configurability Architecture:**
  - Confirmed provider credentials are demo/reference keys for the factory bot; all tenant credentials remain strictly multi-tenant scoped by `tenant_id`.
  - Future tenants purchasing or launching bots configure their own API tokens through the Admin UI (`/admin/#providers`), encrypted into the local vault via `SecretStorage`.
- **Minimal Grid Cards:**
  - Streamlined `.product-card` to display only the product visual/icon monogram, title (2-line clamp), starting price ("From $XX.XX"), and subtle chevron cue.
  - Eliminated bulky inline variant items and long text from cards, preserving a clean 2-column e-commerce grid on mobile devices.
- **Rich Dedicated Product Page (`<dialog id="productDetailDialog">`):**
  - Integrated full-featured product page bottom sheet (mobile) and modal (desktop).
  - Displays product media/icon monogram, delivery eta, category eyebrow, bold title, trust badges or warranty terms (`🛡️ Warranty · X days`), interactive package/variant selector (`.detail-option-card`), quantity stepper (`−`/`+`), full description, and sticky bottom action bar (`Add to cart · $XX.XX`).
  - Added full Arabic RTL translation support in `apps/miniapp/static/locale.js`.
- **Automated Verification:**
  - Created `tests/test_miniapp_product_detail.cjs` verifying minimal grid layout, dialog opening, quantity adjustments, price calculation, variant switching, closing, desktop view, and Arabic RTL.
  - Verified against live container and test server; 100% test pass rate.

