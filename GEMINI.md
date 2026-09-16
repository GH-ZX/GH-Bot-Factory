# GH-Bot-Factory: AI Coding Agent Rules

All AI coding assistants operating in this repository **MUST** adhere to the project constitution, sovereign laws, and workflows defined in:

👉 **[AGENT_MAP.md](file:///home/it/Coding/gh-bot-factory/AGENT_MAP.md)**  
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
8. **Verification:** 100% test pass rate (`.venv/bin/pytest -v`) and clean linter (`.venv/bin/ruff check .`).
9. **Update Tracking:** Every new user prompt and milestone MUST be documented in `docs/prompts/prompt_history.md` and mapped in `AGENT_MAP.md`.
10. **Platform Control Plane:** Platform-operator authority is installation-level and MUST remain separate from tenant RBAC. Tenant roles never mutate plans, subscriptions, billing convergence, or platform audit state.
11. **Portable Hosting:** Laptop/self-hosted operation is first-class. PostgreSQL plus the encrypted local secret vault are portable authoritative state; Redis is rebuildable and `.env` remains destination-owned.
12. **Billing Authority:** Hosted checkout/portal redirects are never subscription authority. Commercial state converges only from authenticated platform operations, verified provider events, or authenticated server-side provider reconciliation. Billing-provider secrets stay environment-only and provider price IDs resolve through the platform catalog.
13. **Release Evidence:** Never call a release production-ready without the canonical PostgreSQL/Docker release gate and applicable staging evidence.
14. **Provider Platform:** Supplier/reseller business logic MUST be vendor-neutral. Provider adapters declare canonical categories/capabilities; tenant provider credentials may enter only through `SecretStorage` write boundaries and MUST never be persisted or returned in plaintext/reference form. Preserve polling/reconciliation paths that work without public ingress. Generic HTTP/OpenAPI mappings MUST remain declarative and SSRF/redirect/secret-safe. Never cross-provider fail over after an ambiguous timeout/transport result that may follow upstream acceptance; reconcile instead. Only canonical provider `COMPLETED` may mark fulfillment complete.
15. **Payment Platform Safety:** External provider callbacks, browser returns, screenshots, and chain observations MUST NOT mutate wallet balances directly. All payment success converges through `PaymentService` and database-enforced settlement idempotency. Amount/currency/provider identity mismatches fail closed. Create-time transport ambiguity MUST NOT trigger blind duplicate creates. Pull reconciliation remains first-class for laptop/self-hosted operation. Experimental gateways require explicit operator acknowledgement, and open-amount stablecoin deposits MUST NOT auto-credit fiat wallets without explicit multi-asset/FX semantics.
16. **Commerce Economics:** `ProviderProductMapping.product_id` always references canonical `Product.id`; `product_variant_id` is only the optional refinement. Customer prices come from server-authoritative immutable quotes. Flexible/open-amount auto-credit is optional per payment method; asset credit preserves exact asset/network identity and fiat credit requires an explicit tenant FX policy. Provider-balance monitoring is read-only evidence and never mutates routing automatically.
17. **Bot Business Profiles:** Advanced bot templates configure the shared core; `_business` references are tenant-validated and runtime-filtered. Legacy bots inherit existing product routing, malformed profiles fail closed, and browser wizard selections are never authority by themselves.
