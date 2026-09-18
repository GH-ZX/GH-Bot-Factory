# ADR-046: Owner setup without synthetic Telegram identities

- Status: Accepted
- Date: 2026-09-19
- Supersedes the synthetic bot/user and implicit tenant-binding behavior described in ADR-042.

An accepted commercial quote creates a new tenant only when its slug is unused. Existing tenant access is never inferred from a slug or username. The authenticated platform operator supplies the confirmed numeric owner Telegram user ID; the username is display metadata. Inactive/deleted users and tenants remain inaccessible. Retrying a linked quote requires that same request identity to already have an active OWNER membership; it never promotes or adds another owner. Ownership transfers remain separate member-management operations.

Onboarding locks the commercial quote row on PostgreSQL, making concurrent same-quote requests converge on one tenant and owner. Unique-constraint races on other records return a retryable conflict after rollback. No synthetic Telegram user IDs or bot IDs are generated. Template intent is retained in tenant settings, exposed as a recommended template in the checklist, and selected in the existing bot provisioning wizard. Telegram getMe and the existing durable provisioning workflow remain the bot identity authority.

A separate `owner_setup` purpose in the Redis-backed AdminLoginService grants permits initial Admin access without a bot. Only the platform-authorized onboarding service issues it. Issuance and consumption require an active OWNER membership, active non-deleted user/tenant, an accepted quote linked to that tenant, and unchanged token version. It retains hashed keys, five-minute TTL and atomic single consumption. The resulting tenant session contains no bot claim or platform authority. Existing Telegram-issued grants continue to require their enabled bot.

Grant storage failure rolls back onboarding and returns HTTP 503; it never fabricates a login code. Successful onboarding commits before returning the grant and marks the response no-store. If a response is lost or a link expires, the platform operator can request a new link for the existing owner. A grant staged before a failed DB commit cannot authenticate absent valid committed identity/linkage. No secrets or codes enter audit details.

No schema migration is needed. Existing synthetic bot rows are not rewritten automatically: they need explicit review and verified replacement. Deployment/export hardening and full staging sale/restore qualification remain separate outstanding work; this repair does not claim those complete.
