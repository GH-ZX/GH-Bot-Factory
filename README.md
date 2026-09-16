# GH-Bot-Factory

Multi-tenant Telegram commerce platform with a shared business core for bots, Telegram Mini Apps, and future administrative clients.

## Architecture

```text
Telegram Bot Runtime        Telegram Mini App
        |                         |
        |                         | HMAC initData -> Bearer JWT
        +-----------+-------------+
                    |
                    v
               FastAPI API
                    |
                    v
              Commerce Core
                    |
      +-------------+-------------+
      |             |             |
   Payments      Providers    Fulfillment
      |             |             |
      +-------------+-------------+
                    |
              Ledger / Orders
                    |
           PostgreSQL + Redis
```

The browser and Telegram clients are untrusted presentation layers. Tenant identity, customer identity, prices, payment amounts, wallet mutations, and fulfillment state remain server-authoritative.

## Current Delivery State

- Foundation and multi-tenancy
- Commerce catalog, orders, state machine, and wallet ledger
- Multi-tenant Aiogram runtime
- Provider routing and durable fulfillment hardening
- Payment abstraction, secure webhooks, settlement/refund idempotency
- Bearer JWT authentication, RBAC, token-version revocation, Telegram Mini App authentication
- JWT signing-secret hardening
- Telegram Mini App storefront with catalog, wallet context, orders, cart checkout, and database-backed checkout idempotency
- Durable post-payment fulfillment staging in the same financial transaction, consumed by a dedicated polling worker
- Live Telegram Mini App launch integration with per-bot HTTPS Web App URLs, persistent menu buttons, and a dedicated bot runtime process
- Wallet top-up UX with purpose-bound PaymentIntents, tenant provider limits, HTTPS gateway handoff, webhook/reconciliation settlement, and automatic wallet refresh
- Native Telegram Stars (`XTR`) checkout with server-validated pre-checkout, exactly-once `successful_payment` settlement, Terms/support handling, and secret-reference bot credentials
- Durable wallet top-up reversal saga with reserve-before-refund accounting, database idempotency, retry/recovery worker, and fail-closed manual review
- Telegram Stars external reversal/chargeback reconciliation with durable provider-event audit, wallet freeze on spent-value debt, and staff resolution workflow
- Storefront catalog discovery with tenant-scoped search, category/availability filters, deterministic pagination, stock-aware variants, and resilient loading/offline states
- Telegram-authenticated admin console with tenant-scoped catalog/order/reconciliation operations and RBAC-backed mutations
- Fulfillment operations center with dead-letter visibility, attempt history, fail-closed safe manual requeue, targeted reconciliation, and operator audit logging
- Admin provider configuration with tenant-scoped supplier routing, runtime-only secret resolution, safe health checks, payment-provider policy management, and hardened Telegram Stars settings
- Financial Resolution Center with durable operator cases, wallet-freeze visibility, optimistic concurrency, audited resolution actions, and OWNER-only guarded false-positive unfreeze
- Tenant member administration with live Membership authorization, owner/admin privilege boundaries, last-active-owner protection, immediate deactivation enforcement, and audited access changes
- Currency-safe tenant analytics for order value, wallet funding/liability, fulfillment/provider performance, operational exposure, and daily activity
- Tenant-scoped Audit Explorer with pagination/filtering, actor context, and defensive secret redaction
- Agent-maintainability protocol with `CURRENT_STATE.md`, concise change log, explicit handoff checklist, ADRs, roadmap, and verbatim prompt history
- Production-readiness layer with immutable non-root/read-only containers, structured observability, dependency-aware health, Redis process heartbeats, PostgreSQL backup/restore drills, Redis-backed abuse controls, secret/dependency scanning, staging failure injection, and release evidence tied to Git SHA + migration head
- Durable Bot Factory provisioning with tenant idempotency, runtime-only Telegram token resolution, `getMe` identity verification, global bot ownership checks, crash recovery, Admin fleet operations, and continuous runtime reconciliation without manual restart
- Versioned Bot Factory templates and guided provisioning wizard with server-validated per-bot branding/modules, signed bot-context storefront theming, Telegram `/admin` entry, and a first-live-trial bootstrap/runbook
- Easy Start first-run web installer with encrypted local vault; the Ubuntu/Telegram live trial reached readiness and the control bot responded
- Bot credential lifecycle with identity-safe verify/rotate, Redis fleet observation, runtime restart controls, tenant fleet limits, launch-readiness checks, and STABLE/CANARY safe rollout
- Phase 9 SaaS foundation with persisted plans/subscriptions, plan-authoritative Bot Factory entitlements, usage visibility, and self-hosted compatibility fallback
- Phase 9.1 platform control plane with installation-level operator authentication, audited plan/subscription management, durable normalized billing-event convergence, and a localhost-first `platformctl` CLI
- Phase 9.2 provider-agnostic SaaS billing with Stripe as the first signed adapter, separate provider price catalog, hosted checkout/customer portal, webhook convergence, and laptop-first pull reconciliation
- Phase 9.3/9.4 commercial feature enforcement, periodic billing reconciliation worker, SaaS health alerts, and support-safe no-impersonation entitlement inspection
- Phase 10 Provider & Integration Platform with canonical supplier operations, constrained Generic HTTP/OpenAPI mappings, deterministic multi-provider routing, normalized offer observations, and async pull reconciliation
- Phase 11 Payments Platform with immutable payment evidence, exactly-once ledger settlement, manual/on-chain methods, NOWPayments, Triple-A, Bybit Pay, Binance Pay, experimental GoZaPay, pull reconciliation, and payment operations health
- Phase 12 Commerce Economics with multi-asset wallets, explicit FX/parity policies, optional GoZaPay flexible-deposit auto-credit, reseller/VIP pricing tiers, wallet holds, immutable price quotes, actual supplier-cost profit attribution, and read-only provider-balance monitoring
- Phase 13 Advanced Bot Factory with reseller/number/account/gift/digital/hybrid templates, a five-step server-validated business wizard, and per-bot provider/payment/routing/pricing/auto-credit enforcement
- Laptop-first portable hosting with quiesced PostgreSQL + encrypted secret-vault export/import for future Laptop -> VPS migration; Redis and destination `.env` remain intentionally non-portable

Start with `docs/operations/CURRENT_STATE.md` when resuming work. See `docs/operations/AGENT_HANDOFF.md` for the documentation/verification protocol, `docs/roadmap.md` for the living roadmap, and `AGENT_MAP.md` for repository invariants and milestone history.

## Easy Start — recommended first trial

For a fresh/self-hosted trial, the preferred path is now:

```bash
python3 scripts/easy_start.py
```

The launcher tolerates transient localhost connection resets/refusals while the API container is still initializing and continues polling until the readiness deadline.

The launcher generates missing local development secrets, repairs the PostgreSQL URL safely, starts the complete Docker stack, waits for readiness, and prints a one-time `/setup/` URL. The web installer creates the first Tenant/OWNER/control bot and stores the Telegram token in the shared encrypted local vault; no `secret_ref` or bootstrap CLI command is required for the normal path.
After initialization, Admin → Bots also accepts a BotFather token directly and moves it into the same encrypted vault; normal self-hosted operation no longer requires manually creating token environment-variable references.

See `docs/runbooks/easy-start-web-setup.md`. The legacy `bootstrap_first_tenant.py` workflow remains available for advanced/operator-controlled installations.

## Telegram Stars configuration

After a tenant bot has been registered and its token exists in `SecretStorage`, configure Stars without copying the token into the database:

```bash
python scripts/configure_telegram_stars.py \
  --tenant-slug <tenant-slug> \
  --terms-url https://example.com/terms
```

The worker process (`python -m apps.worker.main`) handles fulfillment, wallet top-up reversals, Telegram Stars reconciliation, and durable bot provisioning jobs. Run `alembic upgrade head` before starting API, bot runtime, or worker processes. Current migration head: `e1f2a3b4c5d6`.

## Local platform control plane

Easy Start now generates `PLATFORM_ADMIN_TOKEN`. Platform-wide SaaS operations intentionally do not reuse tenant OWNER/ADMIN authority. For laptop/self-hosted operation, use the local CLI instead of exposing another public admin surface:

```bash
python3 scripts/platformctl.py overview
python3 scripts/platformctl.py plans
python3 scripts/platformctl.py tenants
python3 scripts/platformctl.py plan-prices --plan-id <plan-id>
python3 scripts/platformctl.py billing-reconcile --provider stripe
python3 scripts/platformctl.py saas-health
python3 scripts/platformctl.py tenant-entitlements --tenant-id <tenant-id>
```

See `docs/architecture/platform-control-plane.md` and ADR-025.

## Optional hosted SaaS billing

Hosted billing is optional and defaults to disabled, so normal laptop/self-hosted use does not require Stripe, a VPS, or public ingress. When enabled, provider pricing is registered separately from entitlement plans and tenant browsers only receive hosted checkout/customer-portal redirects. Authoritative subscription state converges from signed webhooks or from the local pull reconciler:

```bash
# Example operator-side price registration
python3 scripts/platformctl.py plan-price-add \
  --plan-id <plan-id> \
  --provider stripe \
  --external-price-id <price-id> \
  --currency usd \
  --amount-minor 1900 \
  --interval MONTH

# Laptop-safe convergence when no webhook endpoint is publicly reachable
python3 scripts/platformctl.py billing-reconcile --provider stripe
python3 scripts/platformctl.py saas-health
python3 scripts/platformctl.py tenant-entitlements --tenant-id <tenant-id>
```

Hosted-billing provider credentials stay in `.env`; raw webhook payloads and secrets are not persisted in SaaS records. Commercial access is enforced server-side through the Phase 9.3 entitlement policy, and Phase 9.4 keeps provider state converged with periodic pull reconciliation when public ingress is unavailable. See `docs/architecture/saas-billing.md`, `docs/runbooks/saas-billing-local-first.md`, ADR-027, and ADR-029.

## Provider Platform — Phase 10

Phase 10 is implemented through 10.5. Supplier integrations are capability-driven rather than vendor-name-driven. Tenant provider connections select an adapter plus a canonical category (`NUMBER`, `ACCOUNT`, `GIFT`, `DIGITAL_PRODUCT`, `SERVICE`, or `OTHER`), and domain logic consumes canonical operations/states instead of vendor-specific payloads.

For Swagger/OpenAPI-shaped reseller APIs, the Generic HTTP adapter is a constrained declarative mapper rather than a code-generation/scripting engine. Credentials stay in `SecretStorage`; redirects are disabled; production supplier hosts require HTTPS and an installation allowlist; private/reserved targets and unbounded responses fail closed. Custom reviewed Python adapters remain available for providers that cannot be mapped safely.

Provider routing supports priority, lowest cost, availability, health, weighted, and manual strategies. Automatic cross-provider fallback happens only for failures proven safe before possible upstream order acceptance. Ambiguous timeout/network outcomes enter reconciliation instead of risking duplicate purchases. Cost comparison refuses mixed currencies until explicit FX normalization exists.

Normalized `ProviderOfferSnapshot` records provide fresh cost/availability observations without replacing the tenant catalog as authority. Asynchronous provider orders remain pending until canonical `COMPLETED`; the worker polls active upstream orders so laptop/self-hosted installs can converge without public webhook ingress.

The built-in sandbox/reference drivers allow provider/bot development without live supplier accounts. Concrete vendors such as number providers or Swagger-style reseller APIs should be added behind these contracts only after their authoritative API documentation is reviewed. See `docs/architecture/provider-platform.md` and ADR-030 through ADR-033.

## Laptop -> VPS portability

The portable state contract is PostgreSQL + the encrypted local secret vault. Redis is rebuildable coordination state and `.env` remains destination-owned. Encrypted export is preferred:

```bash
export PORTABLE_GPG_RECIPIENT='<recipient>'
./scripts/export_portable_state.sh
```

Restore on the destination only after its `.env`/Docker runtime is configured:

```bash
./scripts/import_portable_state.sh --confirm /path/to/ghbf-portable-....tar.gz.gpg
```

See `docs/runbooks/laptop-vps-portability.md` and ADR-026.

## Verification and CI

The repository now uses one canonical release contract for humans and coding agents:

```bash
# Fast feedback: lint, compile, handoff consistency, SQLite suite, JS syntax, diff, pip integrity
make verify-fast

# Production-engine verification
export POSTGRES_TEST_DATABASE_URL='postgresql+asyncpg://postgres:postgres@localhost:5432/gh_bot_factory_test'
make verify-postgres

# Required before milestone commit/push
make verify

# Required before a production release candidate
make release-gate
```

GitHub Actions runs the fast gate and a PostgreSQL 17 concurrency gate on pull requests and pushes to `main`. PostgreSQL verification includes `alembic upgrade head`, `alembic check`, and **8 concurrency tests** covering wallet serialization, checkout, payment settlement, durable fulfillment claiming, refunds, wallet creation, last-owner protection, and single-winner durable bot-provisioning claims. The release gate additionally validates Compose, builds the immutable production image, and emits `artifacts/release-evidence.json`; release candidates can require staging E2E/failure injection.

The current migration head is `e1f2a3b4c5d6`. The latest dependency-limited local regression passed **361 tests** with the PostgreSQL-only suite and the two direct-Aiogram modules excluded; Phase 10.1–10.5 focused/compatibility coverage also passed during implementation. Canonical release status still comes only from `make release-gate`. CI stores JUnit results plus runtime/dependency snapshots for traceability. A resolver lockfile remains an explicit deterministic-rebuild follow-up; immutable release images and CI dependency snapshots preserve the exact executed artifact/environment in the meantime.

For the Bot Factory RC operations and canary handoff, follow `docs/runbooks/bot-fleet-release-candidate.md`. The original first-trial flow remains in `docs/runbooks/phase8-first-live-trial.md`. For production/deployment procedures, start with `docs/runbooks/production-deploy-rollback.md`, `docs/runbooks/release-gate.md`, and `docs/runbooks/backup-restore-dr.md`.

## Daily operation / final operator guide

For normal laptop-first operation:

```bash
make start        # first start / Easy Start
make doctor-live  # post-start health/config gate
make status       # Compose status
make logs         # follow API/worker/bot-runtime
make stop         # stop application mutation services
make up           # resume an initialized stack
make upgrade      # migrate + rebuild/restart after a code update
```

The complete create/configure/run/backup/migrate-to-VPS procedure is in `docs/runbooks/operator-guide.md`. Production release qualification is in `docs/runbooks/final-release-checklist.md`.
