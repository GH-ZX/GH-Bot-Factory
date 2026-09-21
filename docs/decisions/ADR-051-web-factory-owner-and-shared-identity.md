# ADR-051 — Web-first factory setup and explicit installation owner

Status: Accepted — 2026-09-22

## Decision

An owner Telegram bot is optional, not a prerequisite for installing or managing the factory. `/setup/` creates a workspace and password identity through setup-code-protected `/api/v1/setup/initialize-web`. It does not create a Bot or call Telegram, and does not require a secret vault until real customer bot/provider credentials are configured. The legacy Telegram initializer remains supported.

`SystemInstallState.operator_user_id` explicitly identifies the installation operator. Only the first-run, locked initialization transaction sets this binding. Tenant roles cannot write it, and migration does not promote existing tenant owners. Platform requests accept an authenticated, unrevoked password session only when its user and tenant match this binding and the installation is initialized. Existing environment platform tokens remain supported for operator automation. Telegram/Mini App/one-time-code sessions never become platform password sessions. Platform audit events identify the web operator. Database backup/restore of installation state preserves this authority; customer tenant bundles must never copy it.

The migration-seeded singleton is locked before any first-run mutation. Concurrent attempts have exactly one winner; a missing seed fails closed with a migration instruction. Any existing tenant, including soft-deleted rows, keeps setup closed. Existing orphan usernames can only be reused after verifying their current password; the setup code is not an account-reset mechanism. Auth/setup validation responses omit input/context values to avoid echoing rejected secrets. Setup secrets are never returned or persisted in browser storage. The query-string setup code is immediately removed; setup sends no referrer. Password hashes use the existing salted PBKDF2 implementation and sessions remain revocable through token_version.

The Admin factory checklist no longer asks the installation owner to connect a bot. Customer workspaces retain their separate launch checklist and verified bot provisioning. The sales UI uses the owner's normal password session without an additional token prompt; environment-token entry remains a legacy fallback and is cleared on logout.

Customer onboarding reads the accepted quote's immutable configuration snapshot (legacy quotes fall back to the inquiry). Template, accent and single-language locale populate the initial template; complete requested scope, including bilingual/report choices, remains in delivery_scope. Requested features never auto-grant integration entitlements or claim implementation. Bilingual runtime and localized reports remain deferred.

## Shared identity

`apps/shared/static/theme.css` owns factory primitives. Builder, Admin/sign-in and setup reference those variables. GH Store's existing logo mark is copied unchanged from `../gh-store-dev/public/gh-store-logo-mark.png`; the sibling project is not modified. Customer bot branding remains tenant-owned and is not overwritten by the factory theme.

## Verification

Fast API tests cover no-bot initialization, wrong setup code, locking, orphan takeover rejection, password operator access, ordinary tenant-owner denial, non-password denial, deletion/revocation and public brief → quote → tenant configuration. PostgreSQL covers simultaneous initialization. Browser checks cover setup states and errors, responsive layouts, theme propagation and existing Admin/builder regressions. Final counts/deployment evidence are recorded in CURRENT_STATE and the five-step checklist. No real-customer installation or production qualification is implied.
