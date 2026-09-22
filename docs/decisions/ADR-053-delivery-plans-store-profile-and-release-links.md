# ADR-053: Delivery plans, public store settings and issue/release links

- Date: 2026-09-22
- Status: Implemented, unverified. Deployment authorized separately by the owner.

## Delivery control plane

`DeploymentHandoff.delivery_details` stores a bounded, versioned delivery plan. Only the
installation operator can read/change delivery plans, scope, readiness and receipt records.
Tenant roles do not gain commercial control-plane access. The checklist combines persisted
configuration with explicitly labelled operator confirmations; it does not run tests or claim
an installation worked. Final receipt requires recorded acceptance/receipt references,
configuration readiness and source runtime deactivation intent, and is explicitly
operator-reported evidence. Desired runtime shutdown is not proof of observed shutdown.

Package requests must match the selected plan image when present. Safe release/destination
notes are added to DELIVERY.json and the localized report. Database passwords, credentials
and bundle passphrases belong to existing secret/export boundaries, never plan fields.

## Tenant profile

The Store settings API accepts only a bounded public profile, notice, recipient instructions,
FAQ and HTTPS policy/support links. It cannot change routing, entitlements, balances or
arbitrary tenant settings. Updates lock the tenant and require the current settings version.
Existing settings are preserved. Bot-specific branding overrides nonblank defaults; all
output is escaped and external links are HTTPS-only. Product warranty, pricing, reseller,
coupon, provider and financial management reuse their existing authoritative services.

## Support to release

Maintenance issues can reference an owned support case and affected version. Linking a case
does not copy private conversation contents. Tenant-owned immutable `customer_releases`
records contain version, digest image, release/migration/rollback notes. `release_issues`
links only tenant-validated issues. Creating a release marks unresolved linked fixes available,
never resolved or installed. Resolving a problem and applying a release remain explicit actions.

Customer update proposals can reference a release. Server-side association checks require
that the release includes the issue and that its immutable details match the proposal.
Existing manual proposals remain compatible. Release/issue records are included in encrypted
single-tenant portability; no automatic cross-tenant sharing is added. New source migration:
`c93eb541da62`, after `b82da430c951`.

## MiniApp

Product details, Help/FAQ/policy screens, asset/spending/payment history and accessible sheets
reuse signed customer identity. Histories scope both ledger and owning wallet to the tenant
and user. Asset amounts serialize as decimal strings and retain explicit network identity.
Opening an old payment reads its existing session; it never creates a new payment implicitly.
Payment status tokens remain protocol values, never translated values used in control flow.

`?preview=1` is a public design preview with sample fixtures. Its API transport never calls
business endpoints and rejects mutations; it has no session or Telegram-authentication
bypass at the server. Normal MiniApp mode still requires signed Telegram authentication.
The preview is visibly labelled and is not a real tenant, trial or acceptance test.

## Verification boundary

The owner requested Docker redeployment now and testing only when explicitly requested later.
No tests, browser runs, gate scripts, commit or push are part of this coding batch. Container
startup/health observation during deployment is not feature acceptance or production evidence.
