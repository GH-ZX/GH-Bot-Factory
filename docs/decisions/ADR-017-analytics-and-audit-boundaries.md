# ADR-017: Operational Analytics and Audit Exposure Boundaries

## Status
Accepted — 2026-09-15

## Context
The tenant admin console needs operational/business visibility without weakening financial invariants, leaking cross-tenant data, or turning dashboard aggregates into a second accounting system. Audit history also needs to be searchable by operators without exposing secrets that might exist in legacy or future detail payloads.

## Decision
Introduce `packages.analytics.AdminAnalyticsService` as a read-only aggregation boundary over authoritative domain tables. Analytics queries are always explicitly tenant-scoped and use bounded UTC windows of 7, 30, or 90 days. Monetary values are grouped by currency; USD, XTR, or any future currency are never added together.

Order analytics distinguish gross captured order value from refunded order value and report a derived net order value. Wallet funding/reversal flows and current wallet liability are shown separately because they are funding/liability metrics, not order revenue. Provider performance is derived from fulfillment attempts and is not treated as payment accounting.

The audit explorer reads `AuditLog` rows under the authenticated tenant, supports bounded pagination/filtering, resolves safe actor display information, and recursively redacts values whose keys look like credentials, tokens, passwords, authorization headers, or API keys before sending details to the browser. This is defense in depth; writers must still avoid logging secret material in the first place.

New time-window query patterns receive explicit composite indexes in migration `7b8c9d0e1f23`. No materialized analytics tables or external analytics store are introduced yet; the operational database remains the source for these bounded admin views until measured load justifies a separate read model.

## Consequences
Analytics remain reproducible, tenant-isolated, and currency-correct without creating an alternative source of financial truth. The design is intentionally simple to operate now and leaves a clean migration path to replicas/materialized read models later if production query volume requires it.
