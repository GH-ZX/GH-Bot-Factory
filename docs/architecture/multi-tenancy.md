# Multi-Tenancy Architecture

Every tenant-owned record in GH-Bot-Factory contains a non-nullable `tenant_id` foreign key with cascade deletion and indexing.

### Tenant Hierarchy & Domain Model

```text
tenant
  ├── bots (Bot instances with token_secret_ref)
  │    └── tenant_telegram_users (Scoped Telegram customer bindings)
  ├── memberships (User + Role: OWNER, ADMIN, MANAGER, STAFF, CUSTOMER)
  ├── categories & products (with ProductVariants and pricing in tenant currency)
  ├── orders & order_items (Governed by OrderStateMachine)
  ├── wallets & ledger_transactions (Double-entry accounting per tenant)
  ├── tenant_provider_configs (Credentials, priority, and markups per provider)
  ├── settings (Branding, locales, currencies, enabled modules)
  └── audit_logs (Immutable activity trails)
```

### Key Multi-Tenancy Invariants

1. **Bot Ownership:**
   - A `Bot` belongs to exactly one `Tenant`.
   - The Telegram numeric bot identity (`telegram_bot_id`) has a global `UNIQUE` constraint across the entire system.
   - It is impossible for Tenant A to bind or receive updates for a bot assigned to Tenant B.

2. **Customer Identity Scoping (`TenantTelegramUser`):**
   - A single human with Telegram ID `12345678` interacting with Store A (Tenant A) and Store B (Tenant B) is provisioned with two completely separate internal `User` records and `Wallet` balances.
   - Customer balances, orders, and cart items in Tenant A can never leak or be visible in Tenant B.

3. **Runtime Tenant Context:**
   - Every incoming Telegram update passes through `TenantResolutionMiddleware`, which constructs an immutable `TenantContext` containing `tenant_id`, `bot_id`, `user_id`, and `correlation_id`.
   - Handlers and application services receive this explicit context; no global mutable thread-local or contextvars are used for security boundaries.
