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
