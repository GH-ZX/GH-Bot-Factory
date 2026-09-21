# GH-Bot-Factory Current State

> **Resume here:** [Customer delivery handoff](RESUME_CUSTOMER_DELIVERY.md) records the owner’s requirements, delivered work, remaining gaps and ordered next steps.

> **Latest deployment — 2026-09-21:** Customer `/build/` redesign from `48a11be` is live after full factory Docker redeployment. All five running services healthy; use `http://10.70.5.5:8010/build/`. See the deployment evidence below.

> First stop for any human or coding agent resuming work. This file distinguishes implemented behavior from externally executed release evidence.

- **Last updated:** 2026-09-22
- **Current phase:** Phase 15 — Admin Identity and Tenant Operations
- **Implementation status:** Steps 1/3 implemented in source: matching Admin identity, one-time customer-owned quotes and tenant operations. Actual customer installation is pending (step 2); shopper polish, automatic packaging/reporting and production qualification remain incomplete (steps 4–6). Earlier phase completion statements below are historical.
- **Current migration head:** `0ce5d2c98e7f`
- **Primary branch:** `main`
- **Previous milestone commit:** `c98fc0c` (`fix(ui): synchronize design system dialogs and bump static cache busters`)
- **Canonical repository:** `git@github.com:GH-ZX/GH-Bot-Factory.git`
- **Verification:** Exact isolated milestone source passed canonical `make verify`: 474 fast + 19 PostgreSQL tests, Ruff, compile, secret/handoff/JavaScript/dependency checks and migration/no drift. Admin operations, Admin/Mini App and setup browser regressions passed. No real customer or production-release acceptance is claimed.
- **Deployment:** Clean source commit `48a11be` rebuilt and deployed to all application services; PostgreSQL and Redis recreated with preserved volumes. Shared application image manifest `sha256:9d11a29119f2cd8c7e8715b04380768706bba3472032010e6c3e0d214d017153`, tagged `48a11be` and `local`. Customer assets `20260921_02` and live browser flow verified at `http://10.70.5.5:8010/build/`. Subdomain DNS remains unavailable from this host.
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
