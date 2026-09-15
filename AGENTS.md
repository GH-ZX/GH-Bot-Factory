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
