---
name: gh-bot-factory-core
description: Core architecture guide, laws of the project, user prompt history, and operational playbooks for GH-Bot-Factory. Use whenever implementing features, debugging, running migrations, or modifying commerce, telegram, provider, and ledger systems in GH-Bot-Factory.
---

# GH-Bot-Factory Core Skill

This skill equips any AI coding agent with the complete institutional memory, architectural laws, and operational protocols of **GH-Bot-Factory**.

## 1. The Core Repository Map

The primary source of truth for the codebase is located at the root of the project:
👉 **[AGENT_MAP.md](file:///home/it/Coding/gh-bot-factory/AGENT_MAP.md)**

All historical user prompts, architectural milestones, and delivery reports are archived in:
👉 **[docs/prompts/prompt_history.md](file:///home/it/Coding/gh-bot-factory/docs/prompts/prompt_history.md)**

---

## 2. Inviolable Laws of GH-Bot-Factory

Every coding agent must memorize and respect these laws at all times:

1. **Language Law:** Always respond in **English** ("answare in english, no answares in arabic").
2. **Multi-Tenancy Law:** Every database query, model, and service operation must be strictly scoped by `tenant_id`. Cross-tenant data leaks are zero-tolerance violations.
3. **Secret Safety Law:** NEVER commit `.env`, bot tokens, supplier credentials, or passwords to Git. Always use `SecretStorage` and reference keys.
4. **Host Safety Law:** Port 8000 is occupied by Portainer; never bind to it. Never stop or modify host Docker containers or Cloudflare Tunnels.
5. **Ledger Law:** Never mutate wallet balances directly. Always use `LedgerService`. Every refund must be strictly idempotent on `(wallet_id, reference_id, reference_type)`.
6. **Authoritative Price Law:** Always validate prices against `ProductVariant.price` in the database. Never trust client-supplied prices.
7. **Fulfillment Law:** Use attempt-scoped idempotency keys (`order:{order.id}:attempt:{attempt_number}`). Transient errors mark `RETRYING` or `UNKNOWN`. Never tell the user they were refunded unless the refund ledger transaction has succeeded. Background jobs must be stored durably in `fulfillment_jobs`.
8. **Verification Law:** Maintain 100% test pass rate (`.venv/bin/pytest -v`) and zero lint warnings (`.venv/bin/ruff check .`) before committing.
9. **Git Remote Law:** Commit coherent milestone commits and push to `origin main` via SSH (`~/.ssh/id_ed25519_ghzx`).

---

## 3. Playbook: How Changes Must Be Executed

```
1. Read Prompt & Check Invariants
       ↓
2. Consult AGENT_MAP.md
       ↓
3. Implement Models/Services in packages/
       ↓
4. Run Alembic Migrations (Batch Mode for SQLite)
   .venv/bin/alembic upgrade head
       ↓
5. Implement Tests in tests/ (StaticPool in-memory SQLite)
   .venv/bin/pytest -v (Must be 100% pass)
       ↓
6. Lint & Format
   .venv/bin/ruff check . --fix
       ↓
7. Update docs/prompts/prompt_history.md & AGENT_MAP.md
       ↓
8. Git Commit & Push via SSH
```

---

## 4. Maintenance Protocol for Updates

Whenever the user provides a new prompt or begins a new milestone:
1. Append the user's prompt verbatim into [`docs/prompts/prompt_history.md`](file:///home/it/Coding/gh-bot-factory/docs/prompts/prompt_history.md).
2. Document the implementation details, affected files, tests, and commit SHA.
3. If new models, tables, or packages were added, update [`AGENT_MAP.md`](file:///home/it/Coding/gh-bot-factory/AGENT_MAP.md).
