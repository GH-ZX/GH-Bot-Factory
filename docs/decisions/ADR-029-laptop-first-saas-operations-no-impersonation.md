# ADR-029: Laptop-First SaaS Operations and No-Impersonation Support

- **Status:** Accepted
- **Date:** 2026-09-16

## Context

GH-Bot-Factory may run permanently on a laptop/self-hosted machine without public ingress, while retaining the option to move to a VPS later. SaaS billing still requires convergence and support tooling even when provider webhooks cannot reach the host. Support also needs to inspect tenant commercial state without creating tenant sessions or bypassing tenant RBAC.

## Decision

1. Periodic provider pull reconciliation is a first-class worker path, not a degraded fallback. Public webhook ingress remains optional.
2. Provider-managed subscriptions persist successful sync timestamp/source and bounded error evidence.
3. Platform SaaS health reports stale synchronization, grace, blocked commercial access, and provider errors using platform authority only.
4. Tenant entitlement inspection never impersonates a tenant user and never mints a tenant JWT. Every inspection is written to `PlatformAuditLog`.
5. `platformctl` remains localhost/SSH friendly so the same operational workflow works on a laptop and later on a VPS.
6. Portable host migration continues to move PostgreSQL plus the encrypted local secret vault while environment/network configuration remains destination-owned.

## Consequences

- A laptop can operate hosted billing safely without exposing a permanent public HTTP endpoint.
- Support state is inspectable without weakening tenant authentication boundaries.
- Moving to a VPS changes hosting/networking, not the SaaS domain model or operational authority model.
