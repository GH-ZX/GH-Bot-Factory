# GH-Bot-Factory: AI Coding Agent Rules

All AI coding assistants operating in this repository **MUST** adhere to the project constitution, sovereign laws, and workflows defined in:

👉 **[AGENT_MAP.md](file:///home/it/Coding/gh-bot-factory/AGENT_MAP.md)**
👉 **[Current Project State](file:///home/it/Coding/gh-bot-factory/docs/operations/CURRENT_STATE.md)**
👉 **[Agent Handoff Protocol](file:///home/it/Coding/gh-bot-factory/docs/operations/AGENT_HANDOFF.md)**
👉 **[Prompt History & Milestones](file:///home/it/Coding/gh-bot-factory/docs/prompts/prompt_history.md)**
👉 **[Skill: gh-bot-factory-core](file:///home/it/Coding/gh-bot-factory/.agents/skills/gh-bot-factory-core/SKILL.md)**

---

## Non-Negotiable Project Laws:
1. **English Only:** All responses and code comments must be in English ("answare in english, no answares in arabic").
2. **Multi-Tenancy:** Every query and model must be scoped by `tenant_id`. No cross-tenant data leakage.
3. **Secrets:** Never commit `.env` or plaintext tokens. Use `SecretStorage`.
4. **Port 8000:** Reserved for Portainer; never bind to it. Never modify host Docker containers or Cloudflare Tunnels.
5. **Ledger & Refunds:** Double-entry ledger invariant. Balance mutations only through `LedgerService`. Every refund must be strictly idempotent.
6. **Pricing:** Authoritative DB pricing only. Never trust client prices.
7. **Fulfillment:** Idempotency keys (`order:{order.id}:attempt:{attempt_number}`). Durable jobs in `fulfillment_jobs`. Transient errors mark `RETRYING`/`UNKNOWN`. Never claim refund until committed.
8. **Verification:** `make verify` must pass, including Ruff, the fast suite, `alembic check`, and PostgreSQL concurrency tests.
9. **Update Tracking:** Every new user prompt and milestone MUST be documented in `docs/prompts/prompt_history.md` and mapped in `AGENT_MAP.md`.
10. **Documentation Gate:** Every milestone MUST update `docs/operations/CURRENT_STATE.md`, `docs/operations/CHANGELOG_AGENT.md`, and `docs/roadmap.md`; use an ADR for architectural/security/accounting decisions. A milestone is not complete when its code is ahead of its handoff documentation.
11. **Release Evidence:** Production readiness requires `make release-gate` with PostgreSQL, immutable image validation, and applicable staging evidence. Never call a release production-ready from SQLite/static checks alone.
12. **Platform Control Plane:** Platform-operator authority is installation-level and MUST remain separate from tenant RBAC. Never let tenant roles mutate plans, subscriptions, billing convergence, or platform audit state.
13. **Portable Hosting:** Laptop/self-hosted operation is a first-class deployment mode. PostgreSQL plus the encrypted local secret vault are portable authoritative state; Redis is rebuildable, `.env` is destination-owned, and laptop-to-VPS moves must use the documented export/import contract rather than ad-hoc copying.
14. **Billing Authority:** Hosted checkout/portal redirects are never subscription authority. Commercial state may converge only from authenticated platform operations, verified provider events, or authenticated server-side provider reconciliation. Billing-provider secrets remain environment-only, and external price IDs must resolve through platform-owned catalog records before entitlements change.
15. **Provider Platform:** Supplier/reseller business logic MUST be vendor-neutral. Provider adapters declare canonical categories/capabilities; tenant provider credentials may enter only through `SecretStorage` write boundaries and MUST never be persisted or returned in plaintext/reference form. Preserve polling/reconciliation paths that work without public ingress. Generic HTTP/OpenAPI mappings MUST remain declarative and SSRF/redirect/secret-safe. Never cross-provider fail over after an ambiguous timeout/transport result that may follow upstream acceptance; reconcile instead. Only canonical provider `COMPLETED` may mark fulfillment complete.
16. **Payment Platform Safety:** External provider callbacks, browser returns, screenshots, and chain observations MUST NOT mutate wallet balances directly. All payment success converges through `PaymentService` and database-enforced settlement idempotency. Amount/currency/provider identity mismatches fail closed. Create-time transport ambiguity MUST NOT trigger blind duplicate creates. Pull reconciliation remains first-class for laptop/self-hosted operation. Experimental gateways require explicit operator acknowledgement, and open-amount stablecoin deposits MUST NOT auto-credit fiat wallets without explicit multi-asset/FX semantics.
17. **Commerce Economics:** `ProviderProductMapping.product_id` always references `Product.id`; use `product_variant_id` only as a refinement. Customer pricing is server-authoritative and frozen in immutable quotes. Stablecoin/crypto auto-credit is optional per payment method and never implies fiat parity: settlement-wallet conversion requires an explicit tenant FX policy, while asset-wallet credit preserves exact asset/network identity. Provider-balance monitoring is advisory only and never changes routing automatically.
18. **Bot Business Profiles:** Advanced bot templates configure the shared core; they do not generate vendor-specific business logic. `_business` references must be tenant-validated server-side and re-filtered at runtime. Legacy bots without `_business` inherit existing product routing; malformed persisted profiles fail closed. Browser wizard choices never become authority by themselves.
