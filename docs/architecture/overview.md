# Architecture Overview

GH-Bot-Factory is a multi-tenant platform.

A tenant represents one customer/store.

Telegram bots are runtime interfaces connected to tenants. Business logic must remain independent of Telegram.

### Core Principles

- Tenant isolation by design.
- Provider abstraction.
- Idempotent financial operations.
- Explicit order state machine.
- Retryable fulfillment.
- Configuration over duplicated code.
- Secrets never committed to Git.
