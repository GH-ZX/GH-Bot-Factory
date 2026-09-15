# ADR-014: Provider Configuration and Secret Boundary

- **Status:** Accepted
- **Date:** 2026-09-15

## Context

Phase 7.2 exposes tenant-scoped supplier and payment-provider configuration through the Admin client. Provider configuration is operational data, but credentials remain secrets. Returning or accepting plaintext credentials through the Admin API would expand the secret-handling surface and make audit logs, browser storage, database JSON, and error reporting possible leakage paths.

Supplier fulfillment also has two runtime paths: initial execution and later reconciliation. Both must resolve the same credential references or reconciliation can fail even when the original supplier call succeeded.

Telegram Stars has an additional constraint: its bot token is embedded in Bot API request URLs by the adapter. Allowing an operator-controlled API-base override alongside a bot-token secret would create a credential-exfiltration primitive.

## Decision

1. Provider secrets are stored only as references. Supplier credentials use `ProviderCredential.secret_ref`; payment providers use `credentials_ref` and `webhook_secret_ref`.
2. Admin read APIs expose only boolean/configured status and credential type. They never return secret references or resolved secret values.
3. Secret references accepted through Admin APIs use an environment-style identifier format and plaintext-looking token values are rejected.
4. Supplier metadata, mapping metadata, and payment settings reject secret-like keys (`secret`, `token`, `password`, `api_key`, `authorization`, `credential`).
5. `ProviderRouter.build_provider_config()` is the single runtime boundary that resolves supplier credential references into ephemeral in-memory adapter configuration. Both order execution and fulfillment reconciliation use this boundary.
6. Provider configuration mutations are tenant-scoped and restricted to ADMIN/OWNER. STAFF may inspect safe operational configuration.
7. All provider/mapping/credential/payment-provider mutations are written to `AuditLog` without secret values or references.
8. Telegram Stars settings are allowlisted. Runtime endpoint overrides such as `api_base_url` are rejected; only `XTR`, native `telegram_invoice`, whole-unit amounts, credential-free HTTPS Terms, and bounded transaction scan pages are accepted.
9. Updating a payment-provider configuration invalidates the tenant/provider cached adapter instance so subsequent operations use the new configuration.

## Consequences

- A database export or Admin API response does not contain supplier/payment secret values.
- Browser clients cannot retrieve secret references for later probing or social-engineering use.
- Supplier reconciliation behaves consistently with original fulfillment execution for credentialed adapters.
- Telegram Stars configuration cannot redirect token-bearing requests to an operator-supplied endpoint.
- Secret rotation is performed by changing the external secret value or its reference, without changing business entities.
- Future secret backends (Vault/KMS/etc.) can replace `EnvSecretStorage` without changing provider persistence models.
