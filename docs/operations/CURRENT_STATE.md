# GH-Bot-Factory Current State

> First stop for any human or coding agent resuming work. This file distinguishes implemented behavior from externally executed release evidence.

- **Last updated:** 2026-09-19
- **Current phase:** Phase 14 — Reliable Owner Onboarding & Single-Tenant Portability Repair
- **Implementation status:** Phase 14 is complete across all sub-phases and repairs: Phase 14.0 Template Guidance & 4-tier actor model (ADR-039), Phase 14.1 Public Configurator on `botfac.gh-store.me/build/` with Server-Authoritative Quote Engine (ADR-040, apex `gh-store.me` untouched), Phase 14.2 Platform Sales Console & Immutable Quotes (ADR-041), Phase 14.3 Customer Onboarding & Setup Checklist (ADR-042), Phase 14.4 Integration & API Marketplace (ADR-043), Phase 14.5 Dedicated Deployment & Source License Handoff (ADR-044), Phase 14.6 Commercial Governance & Release Qualification (ADR-045), Reliable Owner Onboarding Repair (ADR-046), and Encrypted Single-Tenant Portability & Destination Restore Repair (ADR-047).
- **Current migration head:** `e5f6a8b9c1d2`
- **Primary branch:** `main`
- **Latest milestone commit:** `c98fc0c` (`fix(ui): synchronize design system dialogs and bump static cache busters`)
- **Canonical repository:** `git@github.com:GH-ZX/GH-Bot-Factory.git`
- **Verification:** Canonical `make verify` passed: 439 fast tests, 15 PostgreSQL tests including concurrent onboarding and isolated schema restore, Ruff clean, Alembic no drift, and handoff/secret/JS/compile/dependency checks clean. Live deployment verified on rebuilt image `gh-bot-factory:local`.
- **Deployment:** The Compose application serves port 8010 on the server LAN address (`10.70.5.5:8010`), keeping port 8000 reserved for Portainer (Law 4). Cache-busted Admin assets (`v=20260919_04`), Configurator assets (`v=20260919_02`), Mini App assets (`v=20260919_01`), Setup assets (`v=20260919_01`), image rebuild (`gh-bot-factory:local`), and container recreation (`api`, `worker`, `bot-runtime`) are verified live and healthy.
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
