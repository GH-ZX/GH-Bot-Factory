# Architecture Overview

GH-Bot-Factory is a multi-tenant Telegram commerce bot platform.

A **Tenant** represents an isolated customer/store with its own products, orders, wallets, settings, and staff.

Telegram bots act as runtime presentation interfaces connected directly to tenants. Crucially, business logic remains decoupled from Telegram.

```text
Telegram Update
      ↓
Bot Runtime
      ↓
Resolve Bot Identity
      ↓
Resolve Tenant Context
      ↓
Tenant Configuration & Feature Gate
      ↓
Feature / Module Router (Catalog, Orders, Account)
      ↓
Application & Domain Services
      ↓
Commerce Core / Providers / Double-Entry Ledger
```

### Core Principles

- **Tenant Isolation by Design:** Every record has an explicit `tenant_id`. Cross-tenant queries are blocked at both schema and application levels.
- **Bot as a Tenant-Owned Resource:** Multiple bots can point to one tenant, but a single Telegram bot identity can never belong to more than one tenant.
- **Isolated Customer Identities:** Telegram `user_id` does not globally identify an application customer; customer entities are scoped per tenant context via `TenantTelegramUser`.
- **Decoupled Presentation:** Handlers perform zero business logic; they only parse input, invoke domain services with `TenantContext`, and render formatted responses.
- **Provider Abstraction:** Upstream vendors implement a clean `Provider` protocol (`get_products`, `get_balance`, `purchase`, `get_order`) with fallback and health checks.
- **Auditable Double-Entry Ledger:** Wallets are backed by immutable `LedgerTransaction` records (`CREDIT`, `DEBIT`, `REFUND`, `ADJUSTMENT`) allowing complete audit reconstruction.
- **Zero Secret Commits:** Real Telegram bot tokens, database passwords, and API keys are referenced via `SecretStorage` abstractions and never stored in plain text or Git.
