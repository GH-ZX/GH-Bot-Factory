# ADR-038: Repair authentication and financial boundaries

- **Status:** Accepted
- **Date:** 2026-09-16

## Context

The 2026-09-16 repair review ([REPAIR_REVIEW_2026-09-16.md](../operations/REPAIR_REVIEW_2026-09-16.md)) found that the imported Phase 13 update left four boundary gaps: per-bot payment restrictions were bypassable through legacy top-up routes, credited flexible deposits could not observe later provider reversals, failed portable imports restarted applications against partially restored state, and malformed JSON booleans (for example `"false"`) were coerced to enabled. Separately, the Admin console required a Telegram launch context on every visit, so a plain browser visit to `/admin/` failed with "Missing bot_id in admin launch URL" and there was no sanctioned way for a staff member to use the local Admin page from a normal browser.

Each fix crosses a trust or accounting boundary, so the decisions are recorded here.

## Decision

1. **Admin browser sign-in (identity).** Staff authenticate in Telegram (private chat only) and receive a 32-character single-use code valid for five minutes. The code is stored only as a Redis SHA-256 hash and exchanged at `POST /api/v1/auth/admin-code` for the standard session JWT. PostgreSQL remains the sole authority for user/tenant/role/bot validity at exchange time; Redis holds only ephemeral grants and never authority. Group chats never issue codes; replay, expiry, and revoked memberships fail closed. This adds a second **ingestion** boundary for the existing authorization model; it does not create a new authority.
2. **Per-bot funding policy is enforced on every route.** Legacy provider top-up endpoints apply the same `_business` profile restrictions as the method-based routes. Empty selections in a valid profile mean all compatible tenant options; missing/deleted bot context for a signed `bot_id` fails closed instead of inheriting unrestricted defaults. Only bots with no `_business` profile at all keep legacy tenant-wide behavior.
3. **Post-credit reversals freeze instead of debit.** When a credited flexible deposit is later observed `REVERSED` by the provider, reconciliation records durable financial-resolution evidence, freezes the affected wallet, and opens a `FinancialResolutionCase`. The customer is never silently debited after a credit; recovery is an explicit owner action with a resolution note. Settlement stays exactly-once for both settlement and asset wallets.
4. **Failed imports stay offline.** `import_portable_state.sh` restores services only after the database, vault, and migrations all validate. A destructive restore followed by automatic restart would otherwise resume commerce on unverified authoritative state.
5. **Strict configuration types.** Business-profile booleans must be JSON booleans; coercions that turn `"false"` into enabled are prohibited. Malformed persisted profiles fail closed at runtime.
6. **Schema drift is repaired, not suppressed.** Migration `f2a3b4c5d6e7` aligns legacy PostgreSQL JSON columns to the JSONB model definitions so the canonical `alembic check` gate passes without flags.
7. **Customer deposits preserve asset identity.** The Mini App shows exact asset/network/credited value; flexible-deposit auto-credit remains governed by the creation-time method policy snapshot and never implies fiat parity (consistent with ADR-036).

## Consequences

- A staff member can administer a self-hosted installation from a plain browser without a public tunnel or any authentication bypass; Telegram remains the identity root.
- Payment-method restrictions are no longer advisory for profile-managed bots; legacy routes and new routes behave identically.
- Reversal handling after credit adds one reconciliation state transition but keeps settlement exactly-once; unresolved reversals surface as financial cases rather than automatic debits.
- The verification gate is green on this checkout (Ruff, 396 fast tests, 13 PostgreSQL tests, alembic upgrade/check); production-readiness still requires immutable-image release evidence, which these notes do not claim.
