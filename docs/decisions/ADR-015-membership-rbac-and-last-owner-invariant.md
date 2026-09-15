# ADR-015: Membership RBAC and Last-Owner Invariant

- **Status:** Accepted
- **Date:** 2026-09-15

## Context

The Phase 7 Admin client needs tenant member administration without allowing an ADMIN to escalate itself or peers to OWNER, and without allowing a tenant to lose every active owner. Access tokens already carry a tenant/user identity, but authorization is intentionally re-derived from the live `Membership` row on every protected request.

A naive member editor can introduce two high-impact failures:

1. privilege escalation, where an ADMIN grants ADMIN/OWNER to itself or another account outside its authority; and
2. owner lockout, where the last active OWNER is demoted or deactivated, leaving no principal able to recover tenant administration.

Concurrent owner mutations must also not race past the last-owner check.

## Decision

1. Member listing and mutation are restricted to ADMIN/OWNER.
2. ADMIN may manage only memberships below ADMIN and may assign only CUSTOMER, STAFF, or MANAGER.
3. Only OWNER may grant ADMIN or OWNER or modify an existing ADMIN/OWNER membership.
4. Any mutation that would remove an active OWNER must prove at least one other active OWNER remains.
5. Membership mutations serialize on a row lock for the owning `Tenant`, making the last-owner check a tenant-wide critical section on databases that support `FOR UPDATE`.
6. Membership role, active state, and permissions remain server-authoritative. The client submits no tenant identity.
7. Every protected API request re-reads the live membership. Deactivating a membership therefore takes effect on the next request even if an older JWT is still cryptographically valid.
8. Membership mutations are audit logged with actor, target user, changed fields, role transition, and active-state transition.
9. Member APIs do not expose Telegram numeric identifiers. Only operational profile fields required by tenant administrators are returned.

## Consequences

- ADMIN cannot create a peer/higher privilege principal or modify OWNER access.
- OWNER succession is possible while accidental last-owner removal is blocked.
- There is no separate session revocation mechanism required for membership deactivation; live membership authorization already fails closed.
- Tenant member operations are intentionally serialized, which is appropriate for low-frequency administrative writes.
- Future invitation/provisioning workflows must terminate in the same membership boundary rather than creating an alternate role system.
