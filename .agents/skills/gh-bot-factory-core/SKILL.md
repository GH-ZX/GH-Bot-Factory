---
name: gh-bot-factory-core
description: Core architecture guide, laws of the project, user prompt history, and operational playbooks for GH-Bot-Factory. Use whenever implementing features, debugging, running migrations, or modifying commerce, telegram, provider, payment, and ledger systems in GH-Bot-Factory.
---

# GH-Bot-Factory Core Skill

This skill equips any AI coding agent with the complete institutional memory, architectural laws, and operational protocols of **GH-Bot-Factory**.

## 1. The Core Repository Map

The primary source of truth for the codebase is located at the root of the project:
👉 **[AGENT_MAP.md](file:///home/it/Coding/gh-bot-factory/AGENT_MAP.md)**

The latest resumable checkpoint and handoff protocol are:
👉 **[Current State](file:///home/it/Coding/gh-bot-factory/docs/operations/CURRENT_STATE.md)**
👉 **[Agent Handoff](file:///home/it/Coding/gh-bot-factory/docs/operations/AGENT_HANDOFF.md)**
👉 **[Agent Change Log](file:///home/it/Coding/gh-bot-factory/docs/operations/CHANGELOG_AGENT.md)**

All historical user prompts, architectural milestones, and delivery reports are archived in:
👉 **[docs/prompts/prompt_history.md](file:///home/it/Coding/gh-bot-factory/docs/prompts/prompt_history.md)**

Key architecture guides & ADRs:
- **[Payment Architecture & Multi-Client API](file:///home/it/Coding/gh-bot-factory/docs/architecture/payment-architecture.md)**
- **[ADR-006: Multi-Tenant Payment Abstraction](file:///home/it/Coding/gh-bot-factory/docs/decisions/ADR-006-payment-abstraction.md)**
- **[ADR-007: API Authentication & Authorization Hardening](file:///home/it/Coding/gh-bot-factory/docs/decisions/ADR-007-api-authentication-hardening.md)**
- **[ADR-008: Telegram Mini App Storefront & Idempotent Checkout](file:///home/it/Coding/gh-bot-factory/docs/decisions/ADR-008-telegram-miniapp-storefront.md)**
- **[Mini App Storefront Architecture](file:///home/it/Coding/gh-bot-factory/docs/architecture/miniapp-storefront.md)**
- **[Wallet Top-up Architecture](file:///home/it/Coding/gh-bot-factory/docs/architecture/wallet-topups.md)**
- **[ADR-010: Telegram Stars & Durable Wallet Reversal](file:///home/it/Coding/gh-bot-factory/docs/decisions/ADR-010-telegram-stars-refund-reversal-saga.md)**
- **[ADR-011: Telegram Stars External Reversal Reconciliation](file:///home/it/Coding/gh-bot-factory/docs/decisions/ADR-011-telegram-stars-external-reversal-reconciliation.md)**
- **[Analytics & Audit Architecture](file:///home/it/Coding/gh-bot-factory/docs/architecture/analytics-audit.md)**
- **[ADR-017: Analytics & Audit Boundaries](file:///home/it/Coding/gh-bot-factory/docs/decisions/ADR-017-analytics-and-audit-boundaries.md)**
- **[SaaS Plans & Entitlements Architecture](file:///home/it/Coding/gh-bot-factory/docs/architecture/saas-entitlements.md)**
- **[ADR-024: SaaS Plans & Entitlements Boundary](file:///home/it/Coding/gh-bot-factory/docs/decisions/ADR-024-saas-plans-entitlements-boundary.md)**
- **[Platform Control Plane Architecture](file:///home/it/Coding/gh-bot-factory/docs/architecture/platform-control-plane.md)**
- **[ADR-025: Platform Control Plane Boundary](file:///home/it/Coding/gh-bot-factory/docs/decisions/ADR-025-platform-control-plane-boundary.md)**
- **[ADR-026: Portable Self-Hosted State](file:///home/it/Coding/gh-bot-factory/docs/decisions/ADR-026-portable-self-hosted-state.md)**
- **[Commerce Economics Architecture](file:///home/it/Coding/gh-bot-factory/docs/architecture/commerce-economics.md)**
- **[ADR-036: Commerce Economics, Multi-Asset & Flexible Auto-Credit](file:///home/it/Coding/gh-bot-factory/docs/decisions/ADR-036-commerce-economics-multi-asset-and-flexible-autocredit.md)**

---

## 2. Inviolable Laws of GH-Bot-Factory

Every coding agent must memorize and respect these laws at all times:

1. **Language Law:** Always respond in **English** ("answare in english, no answares in arabic").
2. **Multi-Tenancy Law:** Every tenant-owned database query, model, and service operation must be strictly scoped by `tenant_id`. Cross-tenant data leaks are zero-tolerance violations. Explicit platform-control-plane catalog records such as `SaaSPlan` may be global only when documented by an ADR, contain no tenant-owned operational/customer data, and cannot be mutated with tenant authority.
3. **Secret Safety Law:** NEVER commit `.env`, bot tokens, supplier credentials, or passwords to Git. Always use `SecretStorage` and reference keys.
4. **Host Safety Law:** Port 8000 is occupied by Portainer; never bind to it. Never stop or modify host Docker containers or Cloudflare Tunnels.
5. **Ledger & Settlement Law:** Never mutate wallet balances directly. Always use `LedgerService`. Every refund must be strictly idempotent on `uq_refund_idempotency`. Every payment settlement must be strictly idempotent on `uq_settlement_idempotency`. External gateway refunds (`PAYMENT_REFUND`) must remain strictly distinct from supplier fulfillment refunds (`ORDER_FULFILLMENT_REFUND`). Wallet-funding reversals must use the dedicated `WALLET_TOPUP_REVERSAL` saga: reserve/debit internal wallet value idempotently before an external refund, never automatically release the reservation after an ambiguous provider response, and route unresolved cases to reconciliation/manual review. Provider-originated Stars reversals must also be reconciled from authenticated provider transaction history; if already-spent value prevents the local reversal debit, freeze the wallet until operator resolution.
6. **Authoritative Price & Payment Law:** Always validate prices against `ProductVariant.price` and create payment intents strictly from server-authoritative `Order.total_amount`. Never trust client-supplied prices or amounts. Storefront checkout must reject extra client price/identity fields and enforce database-backed idempotency on `(tenant_id, user_id, checkout_idempotency_key)` before wallet debit.
7. **Fulfillment Law:** Use attempt-scoped idempotency keys (`order:{order.id}:attempt:{attempt_number}`). Transient errors mark `RETRYING` or `UNKNOWN`. Never tell the user they were refunded unless the refund ledger transaction has succeeded. Background jobs must be stored durably in `fulfillment_jobs`. Storefront checkout must stage its fulfillment job in the same database transaction as the paid order and wallet debit; supplier execution must not occur on the storefront HTTP path.
8. **Telegram Mini App Authentication Boundary:** Mini App `initData` must be validated server-side using HMAC-SHA256 (`HMAC_SHA256("WebAppData", bot_token)`). Freshness must be checked (`auth_date <= 86400s`, future timestamp <= 300s). Tenant scoping must be derived authoritatively from the owning `Bot` record; never trust client-supplied tenant overrides.
9. **Verification Law:** `make verify` is the canonical release gate. It includes Ruff, the fast suite, handoff consistency, JavaScript syntax, migration upgrade/drift checks, and real-PostgreSQL concurrency tests. Never substitute SQLite-only or dependency-limited verification for a green canonical gate.
10. **Git Remote Law:** Commit coherent milestone commits and push to `origin main` via SSH (`~/.ssh/id_ed25519_ghzx`).
11. **Platform Control Plane Law:** Tenant OWNER/ADMIN authority is never platform authority. Platform plan/subscription/billing mutations require the installation-level platform credential, emit `PlatformAuditLog`, and never store platform/provider secrets or raw secret-bearing webhook payloads. Retired plans are unavailable for new assignment but do not revoke existing subscribers.
12. **Portable State Law:** Laptop/VPS portability consists of PostgreSQL plus the encrypted local secret vault. Redis is rebuildable and `.env` remains destination-owned. Portable bundles contain the vault master key, so encrypted export is preferred and plaintext requires explicit acknowledgement.
13. **The 5 Security Invariants Law:** Client-supplied user/tenant IDs are never authoritative (Identity Law). Every customer operation is authorized against `AuthenticatedPrincipal` (Authorization Law). Telegram `initData` is authenticated cryptographically before deriving identity (Mini App Law). Payment gateway webhooks are authenticated with provider-specific verification against tenant secret (Webhook Law). Authenticated tenant context cannot be overridden (Tenant Law). Customer session authentication (`Authorization: Bearer <JWT>`) and gateway webhook ingestion belong to two separate trust boundaries. Instant session revocation is enforced via `token_version`.
14. **Provider Platform Law:** Supplier/reseller domain logic is vendor-neutral. Provider adapters declare canonical categories/capabilities and domain code consumes canonical states/delivery contracts rather than vendor strings. Tenant provider secret values enter only through write-only `SecretStorage` boundaries; SQL/read APIs expose configuration status rather than secret values/references. Generic HTTP/OpenAPI adapters are constrained declarative mappings: no arbitrary code, redirects, unsafe/private destinations, unbounded responses, or unallowlisted production hosts. Cross-provider failover is allowed only for explicitly pre-order-safe failures; timeout/transport ambiguity remains `UNKNOWN`/reconciliation. Provider-offer snapshots are observations, and mixed-currency cheapest routing fails closed until FX normalization exists. Only canonical `COMPLETED` may fulfill an order; polling/reconciliation must work without public ingress.
15. **Commerce Economics Law:** Provider mappings are product-scoped (`product_id = Product.id`) with optional variant refinement. Price shown/debited is server-authoritative and frozen in an immutable quote. Asset wallets preserve exact asset/network identity; stablecoin-to-fiat conversion is never implicit and requires an explicit tenant FX policy. Flexible-deposit auto-credit is optional per payment method and snapshotted at session creation. Provider balance observations are advisory and never auto-disable suppliers or mutate routing.

---

## 3. Playbook: How Changes Must Be Executed

```
1. Read Prompt & Check Invariants (Including Continuation Prompts)
       ↓
2. Consult AGENT_MAP.md & Relevant Architecture Docs
       ↓
3. Implement Models/Services in packages/ & Routers in apps/api/v1/
       ↓
4. Implement migrations and PostgreSQL-sensitive invariants.
       ↓
5. Run `make verify` with `POSTGRES_TEST_DATABASE_URL` configured for milestone development.
   This includes `alembic upgrade head`, `alembic check`, the fast suite, and PostgreSQL concurrency tests.
   Before production release, run `make release-gate`; require staging/failure-injection evidence for changes to money, durability, databases, Redis, workers, or runtime lifecycle.
       ↓
6. Fix all lint/test/migration-drift failures; never bypass a gate.
       ↓
7. Update CURRENT_STATE.md, CHANGELOG_AGENT.md, roadmap, prompt history, AGENT_MAP/ADR as applicable
       ↓
8. Re-read the handoff state and verify it matches the working tree
       ↓
9. For a release candidate, archive `artifacts/release-evidence.json` and applicable staging/DR evidence
       ↓
10. Git Commit & Push via SSH
```

---

## 4. Maintenance Protocol for Updates

Whenever the user provides a new prompt, continuation directive, or begins a new milestone:
1. Append the user's prompt verbatim into [`docs/prompts/prompt_history.md`](file:///home/it/Coding/gh-bot-factory/docs/prompts/prompt_history.md) (including continuation directives such as *"sorry forvunteruption, use more agents and resume"*).
2. Document the implementation details, affected files, tests, and commit SHA.
3. Update `docs/operations/CURRENT_STATE.md`, `docs/operations/CHANGELOG_AGENT.md`, and `docs/roadmap.md` for every milestone.
4. If new models, tables, routes, packages, trust boundaries, or agent rules were added, update [`AGENT_MAP.md`](file:///home/it/Coding/gh-bot-factory/AGENT_MAP.md) and create corresponding ADRs under `docs/decisions/`.
5. Never report a canonical green gate when dependencies were skipped or unavailable; record runnable and blocked verification separately.



## 5. Production-Readiness Handoff

Phase 8 begins only from the Phase 7.9 checkpoint. Preserve the immutable-image model, Redis-backed production rate limits, liveness/readiness distinction, process heartbeats, backup restore drills, PostgreSQL concurrency gate, and machine-generated release evidence. Provisioning scale must never weaken these controls.


## 6. Bot Factory Provisioning Handoff

Phase 8 bot creation is a durable desired-state workflow. Admin routes enqueue `BotProvisioningJob`; they do not call Telegram inline. Store only `token_secret_ref`, verify identity through Telegram `getMe`, treat `telegram_bot_id` as global ownership authority, bind idempotency keys to request fingerprints, and let the bot runtime continuously reconcile PostgreSQL Bot rows into polling tasks. Cross-tenant identity conflicts and secret leakage fail closed. See ADR-020 and `docs/architecture/bot-factory-provisioning.md`.

## 7. Templates, Branding & First Trial Handoff

Normal new-bot creation uses the versioned template catalog in `packages/factory/templates.py`; do not reintroduce browser-authored raw JSON as the standard UX. Template versions are immutable, provenance lives in `Bot.config._factory`, and per-bot storefront branding is resolved from the signed `bot_id` JWT claim produced only after verified Telegram initData authentication. Branding/config edits preserve credential references and remain ADMIN/OWNER + AuditLog operations. For the first live environment exercise follow `docs/runbooks/phase8-first-live-trial.md`, then record the external evidence in `CURRENT_STATE.md` before starting Phase 8.2.

## 8. Easy Start / First-Run Installer Handoff

Web-first factory setup (ADR-051) is now the default: `/setup/` creates a password account and explicit installation-operator binding without a Telegram bot. Never require an owner bot or `/admin` token for factory sign-in. Customer bots are connected later with verified credentials. Tenant roles do not imply installation authority.

For legacy Telegram-based self-hosted trials, prefer `python3 scripts/easy_start.py` and `/setup/` over the legacy manual `.env` + `bootstrap_first_tenant.py` sequence. The installer is one-time, setup-code protected, and database-locked. Telegram tokens accepted by setup must be verified with `getMe` and written only through `SecretStorage`; they must never enter PostgreSQL, AuditLog details, API responses, or logs. The default self-hosted runtime resolves environment secrets first and then the shared encrypted local vault. Per-tenant `miniapp_public_url` / `admin_public_url` settings may override process defaults. See ADR-022 and `docs/runbooks/easy-start-web-setup.md`.


## 9. SaaS Plans, Platform Control Plane & Portability Handoff

Phase 9.0 introduced the split between platform-global commercial catalog state and tenant-owned subscription/usage state. Phase 9.1 adds the independent installation-level platform trust boundary, `PlatformAuditLog`, durable normalized `BillingEvent` convergence, and localhost-first `platformctl`. Tenant Admin remains read-only for plan/usage and can never self-assign commercial authority. Plan inactivity means retired from new assignment; existing subscribers remain valid until explicitly moved/cleared. Self-hosted/laptop portability is PostgreSQL + encrypted local secret vault, not Redis or `.env`; use the versioned portable export/import scripts and keep destination credentials/public URLs destination-owned. See ADR-024/025/026, `docs/architecture/platform-control-plane.md`, and `docs/runbooks/laptop-vps-portability.md`.

## 10. Provider & Integration Platform Handoff

Phase 10 is implemented through 10.5. Keep adapter/vendor details behind `packages/providers`; bot/commerce/fulfillment code uses canonical categories, capabilities, order states, delivery artifacts, routing policy, and normalized offer observations. Generic HTTP/OpenAPI integrations are data-driven constrained mappings, never executable scripting. Preserve SSRF/redirect/allowlist/credential-redaction controls. Never fail over to a second supplier after an ambiguous timeout or network failure that may follow upstream acceptance. Only canonical `COMPLETED` may mark fulfillment complete; pending/unknown upstream orders converge through the durable reconciliation worker so laptop/self-hosted operation does not depend on webhooks. See ADR-030/031/032/033 and `docs/architecture/provider-platform.md`.
