# GH-Bot-Factory Current State

> First stop for any human or coding agent resuming work. This file distinguishes implemented behavior from externally executed release evidence.

- **Last updated:** 2026-09-16
- **Current phase:** Repair baseline — Admin access & financial hardening
- **Implementation status:** Repairing the imported update. New Phase 13 development is on hold until the user explicitly requests it. Existing Phase 13 code remains in the working tree; its earlier completion declarations are historical and do not establish release acceptance.
- **Current migration head:** `f2a3b4c5d6e7`
- **Primary branch:** `main`
- **Canonical repository:** `git@github.com:GH-ZX/GH-Bot-Factory.git`
- **Repair notes:** [Changes, verification, deployment, and remaining recommendations](REPAIR_PATCH_NOTES_2026-09-16.md).
- **Verification:** Canonical gate green on 2026-09-16 (see [repair patch notes](REPAIR_PATCH_NOTES_2026-09-16.md)): Ruff clean; 396 fast tests passed; 13 PostgreSQL tests passed on disposable `ghbf_repair_test` (127.0.0.1:55439), including the five new economics concurrency races; alembic upgrade/check clean at `f2a3b4c5d6e7`; handoff/secret/JS/compile/`pip check` gates passed. Docker release-gate, staging, restore drill, and live-provider evidence remain unclaimed; no production-readiness claim.
- **Deployment:** The existing Compose application serves port 8010. Source edits do not update its immutable image. No existing application container has been restarted or modified during repair work.

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
   - legacy routing inheritance plus malformed-profile fail-closed behavior
   - laptop/VPS operator guide and host `doctor` checks
16. Agent-maintainability layer: current-state checkpoint, handoff protocol, changelog, ADRs, roadmap, prompt history, and machine consistency check.

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

User authorized committing and pushing the imported baseline plus repairs on 2026-09-17. Canonical verification was rerun successfully (396 fast and 13 PostgreSQL tests). Deployment remains operator-run under Law 4: back up PostgreSQL and the vault, build the new image before migrating, and restart application services only after a successful migration. Collect `make release-gate`, staging failure-injection, and restore-drill evidence before production cutover. Deferred recommendations and acceptance order live in [REPAIR_PATCH_NOTES_2026-09-16.md](REPAIR_PATCH_NOTES_2026-09-16.md). Do not begin new Phase 13 work until the user explicitly requests it.
