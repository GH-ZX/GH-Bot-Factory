---
name: gh-bot-factory-core
description: Core architecture guide, laws of the project, user prompt history, and operational playbooks for GH-Bot-Factory. Use whenever implementing features, debugging, running migrations, or modifying commerce, telegram, provider, payment, and ledger systems in GH-Bot-Factory.
---

# GH-Bot-Factory Core Skill

This skill equips any AI coding agent with the complete institutional memory, architectural laws, and operational protocols of **GH-Bot-Factory**.

## 1. The Core Repository Map

The primary source of truth for the codebase is located at the root of the project:
👉 **[AGENT_MAP.md](file:///home/it/Coding/gh-bot-factory/AGENT_MAP.md)**

All historical user prompts, architectural milestones, and delivery reports are archived in:
👉 **[docs/prompts/prompt_history.md](file:///home/it/Coding/gh-bot-factory/docs/prompts/prompt_history.md)**

Key architecture guides & ADRs:
- **[Payment Architecture & Multi-Client API](file:///home/it/Coding/gh-bot-factory/docs/architecture/payment-architecture.md)**
- **[ADR-006: Multi-Tenant Payment Abstraction](file:///home/it/Coding/gh-bot-factory/docs/decisions/ADR-006-payment-abstraction.md)**
- **[ADR-007: API Authentication & Authorization Hardening](file:///home/it/Coding/gh-bot-factory/docs/decisions/ADR-007-api-authentication-hardening.md)**

---

## 2. Inviolable Laws of GH-Bot-Factory

Every coding agent must memorize and respect these laws at all times:

1. **Language Law:** Always respond in **English** ("answare in english, no answares in arabic").
2. **Multi-Tenancy Law:** Every database query, model, and service operation must be strictly scoped by `tenant_id`. Cross-tenant data leaks are zero-tolerance violations.
3. **Secret Safety Law:** NEVER commit `.env`, bot tokens, supplier credentials, or passwords to Git. Always use `SecretStorage` and reference keys.
4. **Host Safety Law:** Port 8000 is occupied by Portainer; never bind to it. Never stop or modify host Docker containers or Cloudflare Tunnels.
5. **Ledger & Settlement Law:** Never mutate wallet balances directly. Always use `LedgerService`. Every refund must be strictly idempotent on `uq_refund_idempotency`. Every payment settlement must be strictly idempotent on `uq_settlement_idempotency`. External gateway refunds (`PAYMENT_REFUND`) must remain strictly distinct from supplier fulfillment refunds (`ORDER_FULFILLMENT_REFUND`).
6. **Authoritative Price & Payment Law:** Always validate prices against `ProductVariant.price` and create payment intents strictly from server-authoritative `Order.total_amount`. Never trust client-supplied prices or amounts.
7. **Fulfillment Law:** Use attempt-scoped idempotency keys (`order:{order.id}:attempt:{attempt_number}`). Transient errors mark `RETRYING` or `UNKNOWN`. Never tell the user they were refunded unless the refund ledger transaction has succeeded. Background jobs must be stored durably in `fulfillment_jobs`.
8. **Telegram Mini App Authentication Boundary:** Mini App `initData` must be validated server-side using HMAC-SHA256 (`HMAC_SHA256("WebAppData", bot_token)`). Freshness must be checked (`auth_date <= 86400s`, future timestamp <= 300s). Tenant scoping must be derived authoritatively from the owning `Bot` record; never trust client-supplied tenant overrides.
9. **Verification Law:** Maintain 100% test pass rate (`.venv/bin/pytest -v`) and zero lint warnings (`.venv/bin/ruff check .`) before committing. (Current baseline: 106/106 passing tests).
10. **Git Remote Law:** Commit coherent milestone commits and push to `origin main` via SSH (`~/.ssh/id_ed25519_ghzx`).
11. **The 5 Security Invariants Law:** Client-supplied user/tenant IDs are never authoritative (Identity Law). Every customer operation is authorized against `AuthenticatedPrincipal` (Authorization Law). Telegram `initData` is authenticated cryptographically before deriving identity (Mini App Law). Payment gateway webhooks are authenticated with provider-specific verification against tenant secret (Webhook Law). Authenticated tenant context cannot be overridden (Tenant Law). Customer session authentication (`Authorization: Bearer <JWT>`) and gateway webhook ingestion belong to two separate trust boundaries. Instant session revocation is enforced via `token_version`.

---

## 3. Playbook: How Changes Must Be Executed

```
1. Read Prompt & Check Invariants (Including Continuation Prompts)
       ↓
2. Consult AGENT_MAP.md & Relevant Architecture Docs
       ↓
3. Implement Models/Services in packages/ & Routers in apps/api/v1/
       ↓
4. Run Alembic Migrations (Batch Mode for SQLite)
   .venv/bin/alembic upgrade head
       ↓
5. Implement Tests in tests/ (StaticPool in-memory SQLite)
   .venv/bin/pytest -v (Must be 100% pass, e.g. 106/106)
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

Whenever the user provides a new prompt, continuation directive, or begins a new milestone:
1. Append the user's prompt verbatim into [`docs/prompts/prompt_history.md`](file:///home/it/Coding/gh-bot-factory/docs/prompts/prompt_history.md) (including continuation directives such as *"sorry forvunteruption, use more agents and resume"*).
2. Document the implementation details, affected files, tests, and commit SHA.
3. If new models, tables, routes, or packages were added, update [`AGENT_MAP.md`](file:///home/it/Coding/gh-bot-factory/AGENT_MAP.md) and create corresponding ADRs under `docs/decisions/`.

