# GH-Bot-Factory

Multi-tenant Telegram commerce Bot Factory.

## Architecture

The platform separates the business core from Telegram presentation.

```text
Telegram Bots
      |
      v
Bot Runtime
      |
      v
Commerce Core
      |
      +---- Tenants
      +---- Products
      +---- Orders
      +---- Payments
      +---- Providers
      +---- Fulfillment
      |
      +---- PostgreSQL
      +---- Redis
```

## Roadmap

1. Foundation
2. Multi-tenancy
3. Commerce domain
4. Telegram runtime
5. Provider engine
6. Fulfillment
7. Payments
8. Admin
9. Bot provisioning
10. White-label SaaS
