# ADR-054: Factory hardening and operation evidence

Status: accepted for implementation; verification deferred by owner.
Date: 2026-09-22

## Decision

Customer delivery remains independent Docker + PostgreSQL/Supabase. Package operations
validate the file manifest and destination configuration before Docker calls. Configuration
generates a unique Compose project name. Local operation locks and private receipts prevent
blind duplicate imports/updates; interrupted operations require diagnosis/recovery rather
than automatic retries. Update declarations bind exact previous/new digests and database/vault
backup checksums. The optional host backup helper encrypts both artifacts and leaves writes
stopped. It requires an operator-configured matching PostgreSQL service and verified GPG key.
Checksums establish file integrity, not publisher identity or successful restore acceptance.

Tenant operational attention is derived from authoritative rows and ephemeral fleet observations.
It covers delayed orders/payments, failed jobs, financial cases, supplier balance/health, bot
credentials/runtime and backup evidence. It never changes routing, financial state or fulfillment.
A bounded tenant settings map stores 24-hour acknowledgements keyed to observation fingerprints;
acknowledgement does not remove the alert or mark it resolved. Related writes are audited.
Configuration checks are not end-to-end acceptance, and bounded lists disclose truncation.

Owner-reported installation/backup evidence is versioned in tenant settings. Actual schema
revisions are read separately. Reports omit free text, credentials and shopper data. Temporary
diagnostic grants remain revocable, read-only, issue-bound and capped to three active grants
per issue. Installed/rolled-back maintenance updates require matching image/schema evidence.
These records do not execute or prove customer-server changes. Destination imports remove
source installation evidence and attention acknowledgements.

Tenant admins can pause new sales through bounded store settings. Shared checkout checks this
policy after existing idempotent-order lookup, so retries and existing orders remain available.
A shared tenant row lock keeps policy updates ordered with new checkout transactions. This is
not a stop-all-workers or stop-all-payments switch; those need the explicit deployment workflow.

MiniApp stores one unresolved money request per tenant/user in session storage before sending.
Explicit recovery reuses its immutable payload/key; changed requests are blocked until recovery.
Ambiguous errors preserve the request. The server's idempotency constraints remain authority.
Browser storage is best effort across a browser session, not durable cross-device coordination;
server state and history remain authoritative. There is no automatic mutation retry.

Security changes make API responses non-cacheable, strip submitted inputs/custom value-error
messages from validation responses, atomically increment/expire Redis throttle buckets, stop
body buffering after disconnect and allow authenticated staff to revoke all their account sessions.
Fulfillment manual requeue now locks and explicitly scopes the job/order used for its safety decision.
Platform workspace aggregation retains installation-level authority and explicit tenant scoping
for maintenance joins; its progress labels distinguish configuration, references and receipt.

## Consequences and deferred evidence

No schema migration is needed; source/live head remains c93eb541da62. Generate fresh packages
for these script changes. Existing packages do not gain new lifecycle receipts automatically.
No tests or acceptance runs were performed. Required later checks include PostgreSQL lock order,
checkout/payment retries, throttle failures, cross-tenant/RBAC access, package import/update/restore,
frontend/RTL/keyboard behavior and secret redaction. This ADR is not production-release evidence.
