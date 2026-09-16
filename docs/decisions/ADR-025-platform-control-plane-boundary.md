# ADR-025: Installation-Level Platform Control Plane Boundary

- **Status:** Accepted
- **Date:** 2026-09-15
- **Decision scope:** Phase 9.1 platform administration and billing convergence

## Context

Tenant OWNER/ADMIN roles administer one tenant. They must never become authority for global SaaS plans, cross-tenant subscription assignment, or provider billing convergence. A separate trust boundary is required before connecting any external billing provider.

The project must also remain convenient for a laptop-hosted self-managed installation, where the safest operator surface is localhost/SSH rather than another publicly exposed web console.

## Decision

1. Platform operations use a dedicated installation secret, `PLATFORM_ADMIN_TOKEN`, presented through `X-GHBF-Platform-Token`.
2. Tenant Bearer JWTs and tenant RBAC are never accepted as platform authority.
3. Missing or weak platform credentials fail the platform API closed with no fallback to tenant OWNER privileges.
4. `scripts/easy_start.py` generates a strong platform token for local/self-hosted installations. Existing deployments without one continue serving tenant workloads; only the platform control plane remains unavailable until configured.
5. `scripts/platformctl.py` is the preferred operator client. It reads the token from local `.env` and targets the localhost API, so laptop hosting and later VPS-over-SSH use the same workflow.
6. Global platform mutations are written to `PlatformAuditLog`, separate from tenant `AuditLog`.
7. `SaaSPlan.is_active = false` means retired from new assignment. It does not revoke existing subscribers; existing subscriptions continue resolving against the retired plan until explicitly moved or cleared.
8. Billing provider adapters must normalize and authenticate provider events before calling the domain convergence boundary. Browser checkout state is never subscription authority.
9. `BillingEvent` stores an idempotency key/fingerprint and normalized non-secret metadata, never raw provider webhook payloads or credentials.
10. Reusing `(provider, external_event_id)` with a different normalized payload is a conflict and must not mutate subscription state.

## Consequences

- Platform authority is cryptographically and conceptually separate from tenant authority.
- No public platform UI is required for laptop/self-hosted operation.
- Future Stripe/Paddle/other adapters can converge into one provider-neutral subscription domain.
- Retiring a plan is operationally safe for existing tenants.
- Platform audit evidence survives tenant membership changes and tenant-level audit policy.
