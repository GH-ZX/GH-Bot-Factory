# ADR-012 — Admin Client RBAC and Tenant Boundary

- Status: Accepted
- Date: 2026-09-15

## Context

Phase 7 introduces an operator-facing client for tenant staff. The platform is multi-tenant and already issues tenant-bound Bearer JWTs from verified Telegram Mini App `initData`. The admin client must not create a second authentication system, trust a client-supplied tenant identifier, or give read-only staff the same mutation authority as managers.

## Decision

1. `/admin/` is a separate static client, but it reuses the existing Telegram Mini App authentication exchange and Bearer JWT session model.
2. Every admin API derives `tenant_id` and `user_id` exclusively from `AuthenticatedPrincipal`.
3. `STAFF`, `MANAGER`, `ADMIN`, and `OWNER` may read tenant operations data.
4. Catalog mutations require `MANAGER`, `ADMIN`, or `OWNER` through `require_manager_or_above`.
5. Product/category/variant writes create tenant-scoped `AuditLog` records with the acting user, action, resource and changed fields.
6. Foreign-tenant resource identifiers are treated as not found. The API never switches tenant context based on request payload or query parameters.
7. Admin UI authorization is not trusted. Buttons may be hidden client-side for usability, but server-side role checks are authoritative.

## Initial Phase 7.0 Scope

- Tenant operations bootstrap and counts.
- Category read/create/update.
- Product search/list/create/update.
- Variant create/update.
- Tenant-scoped order list with latest durable fulfillment-job status.
- Tenant-scoped payment reconciliation event queue.
- Static Telegram-aware admin console at `/admin/`.

## Consequences

- A staff member can inspect operations but cannot mutate catalog state.
- The same token/session revocation and tenant-isolation invariants used by the storefront apply to the admin client.
- No database migration is required for Phase 7.0 because existing `AuditLog`, commerce, fulfillment and reconciliation tables are sufficient.
- Later Phase 7 slices can add provider configuration, fulfillment actions, member management, analytics and richer audit views without weakening the trust boundary.
