# ADR-003: Multi-Tenant Telegram Runtime Architecture

- **Status:** Accepted
- **Date:** 2026-09-15
- **Deciders:** Architecture Team

---

## 1. Context & Problem Statement

GH-Bot-Factory requires a runtime layer capable of operating hundreds of independent Telegram bots concurrently. Each bot must represent a specific tenant's storefront, use tenant-specific branding and products, maintain separate wallets, and run securely without cross-tenant data leakage or leaking Telegram bot tokens.

---

## 2. Decision Drivers

1. **Strict Multi-Tenancy:** Zero possibility for Tenant A to see or manipulate Tenant B's customers, orders, or wallets.
2. **Decoupled Architecture:** Business and commerce logic must remain completely independent of Telegram's presentation layer.
3. **Bot Token Protection:** Plaintext Telegram bot tokens must never be written to Git, logged in console outputs, or exposed through REST APIs.
4. **Transport Independence:** Runtime must support polling for development and seamless webhook routing in production.

---

## 3. Key Design Decisions

### A. Why `Bot` is a Tenant-Owned Resource
A bot is treated as a presentation interface owned by exactly one `Tenant`.
- Multiple bots may map to the same tenant (e.g. main sales bot, VIP bot, backup support bot).
- However, the Telegram bot identity (`telegram_bot_id`) has a strict database `UNIQUE` constraint preventing the same bot from ever being assigned to more than one tenant.

### B. Tenant Resolution Mechanism
Every bot instance registered in `BotRuntimeManager` knows its primary database `bot_id`.
Incoming updates pass through `TenantResolutionMiddleware` which queries the database for the bot's metadata, checks `bot.is_enabled`, and instantiates an explicit, immutable `TenantContext` containing:
- `bot_id`
- `tenant_id`
- `user_id`
- `correlation_id`
- `config` (currency, branding, enabled modules)

No global mutable state or ambient thread-locals are used.

### C. Why Telegram `user_id` is NOT a Global Application Identity
In a multi-tenant platform, customer accounts must be strictly isolated per tenant.
- If user `@john` (Telegram ID `123456`) buys from Store A, his balance and order history belong strictly to Store A.
- If the same user `@john` visits Store B, Store B must not see Store A's order history or balance.
- Therefore, the system maintains `TenantTelegramUser` which maps `(tenant_id, bot_id, telegram_user_id)` to a tenant-scoped internal `User` and `Membership`.

### D. Polling vs. Future Webhook Architecture
- In development and local staging, `BotRuntimeManager.start_polling()` launches concurrent polling loops for each enabled bot.
- In production, the runtime integrates with FastAPI via `Dispatcher.feed_update(bot, update)` behind Cloudflare Tunnel without any changes to handlers or middlewares.

### E. Secret-Storage Abstraction
Plaintext tokens are never stored in database model fields. Instead, the `Bot` model stores a `token_secret_ref` string (e.g., `ENV_BOT_STORE_ALPHA_TOKEN`).
The runtime queries a `SecretStorage` protocol (`get_secret(ref)`):
- For development: `EnvSecretStorage` resolves from environment variables or secure memory.
- For production: Swappable with HashiCorp Vault, AWS Secrets Manager, or Google Secret Manager.

---

## 4. Consequences

### Positive:
- Clean separation of concerns between Telegram UI and commerce logic.
- Total tenant isolation verified by unit and regression tests.
- High testability: handlers and middlewares can be tested in isolation using mock update objects without active Telegram network connections.

### Negative / Trade-offs:
- One additional database lookup per new Telegram user to resolve `TenantTelegramUser` binding (mitigated by indexing and future Redis caching).
