# Multi-Tenancy

Every tenant-owned record will contain a tenant identifier.

The application layer must enforce tenant scoping.

### Future Database Model

```text
tenant
  |
  +-- users
  +-- bots
  +-- products
  +-- orders
  +-- payments
  +-- providers
  +-- settings
  +-- audit_logs
```

Cross-tenant access must be impossible through normal application paths.
