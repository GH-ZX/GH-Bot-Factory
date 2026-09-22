# ADR-052: Customer-owned delivery and consented maintenance

- Date: 2026-09-22
- Status: Implemented in source; unverified and not deployed
- Supersedes no accounting, payment, platform-authority or release-evidence law.

## Decision

Customer installations remain independent. No factory-owner Telegram bot, subscription,
permanent factory connection, SSH backdoor, remote shell or automatic patch executor is required.

MiniApp palettes live in `apps/shared/static/store-themes.json`; Admin previews and the
shopper interface consume those same tokens. Bot branding accepts only the five known
`miniapp_theme` keys. Merchant text and supplier delivery data are not automatically translated.
The shopper interface has English/Arabic labels and RTL layout; tenant default language can
be overridden for that shopper's browser session. Factory branding remains separate.

Maintenance has four tenant-owned tables: `maintenance_issues`, `maintenance_events`,
`diagnostic_grants`, and `customer_updates`. Tenant owners manage these records; tenant staff
can read issue history. Every record lookup is scoped to authenticated tenant identity.
These roles never grant platform-operator authority.

A diagnostic grant is a 256-bit random capability with a one-way SHA-256 digest stored in
SQL. The clear code is returned once with `Cache-Control: no-store`; it is neither persisted
in browser storage nor recoverable from the database. It is not a provider secret: unlike
provider credentials, diagnostic capabilities have no decryption/recovery requirement.
Requests use `X-Support-Token`, never query-string tokens. The digest resolves exactly one
tenant/issue and grants only allowlisted schema revisions, issue status and bot counts.
No raw logs, issue text, customer profiles, orders, balances, configuration or credentials are
included. Reads and revocation lock the grant row; successful reads are audited in the same
transaction. Expiry is 1–72 hours and owners can revoke. Existing auth-rate limits apply.
Input validation omits rejected token values. Revocation cannot recall already-returned data.

Actual fixes ship as versioned application images. Issue events preserve diagnosis, scope
(shared core versus tenant customization), fixed version and owner-confirmed resolution.
There is no automatic cross-tenant issue copying: shared reproductions must be sanitized.

Update records accept immutable image references and enforce optimistic versions and the
sequence PROPOSED → APPROVED → BACKED_UP → INSTALLED, with cancellation/rollback branches.
These are **owner-reported records**, not a deployment agent or verified evidence. Approval,
backup reference, migration notes and completion evidence are retained. Rollback instructions
require compatible image/database/vault restoration; a downgrade command is never assumed safe.

## Packaging and portability

Platform-only package generation wraps the existing encrypted export, after checksum and
bounded-size checks. It adds digest-based Compose, destination configuration, a complete
fresh-database SQL file, setup/update scripts, manifest checksums and an English/Arabic HTML
report. An actual application image must be published separately; the ZIP is not an image
archive and no image has been built for this milestone. Offline image transport is explicitly
separate and requires identity validation.

`01-database.sql` compiles the current SQLAlchemy PostgreSQL metadata, seeds the uninitialized
installer singleton, and stamps the single current Alembic head. It is strictly for empty
customer databases. It enables RLS without browser policies on backend tables and revokes
anonymous/authenticated API-role privileges when those roles exist. The server uses a private
owner database connection. Existing installations upgrade through Alembic, not this file.
This new bootstrap path requires PostgreSQL schema equivalence and Supabase acceptance
before release; code generation itself is not proof of parity.

The importer now permits exactly one pristine uninitialized installer singleton while still
rejecting any existing customer data or installation binding. It locks/reuses that singleton,
never silently deletes initialized state, and preserves the no-platform-operator import rule.

Issue/event/update history travels inside encrypted tenant bundles. Diagnostic grants are
explicitly excluded and cannot survive a move. Pending source update approvals are cancelled
on destination import: consent is installation-specific. Existing installed/rolled-back records
remain historical records, not claims about the new destination. Adding tables changes the
schema fingerprint; old snapshots must be regenerated with matching release code.

## Evidence and limits

No tests, static verification gates, browser checks, migration execution, Docker builds,
real-customer checks or deployment were run, per owner instruction. No production readiness
is claimed. The existing deployed migration remains `a71c9e23b840`; the new source head is
`b82da430c951`. PostgreSQL concurrency, package/schema parity, encryption/import, UI behavior,
Supabase deployment and release-image evidence remain required when the owner authorizes them.
