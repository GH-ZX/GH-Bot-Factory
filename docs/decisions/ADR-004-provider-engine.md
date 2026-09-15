# ADR-004: Provider Engine, Routing Abstraction, and Credential Safety

- **Status:** Accepted
- **Date:** 2026-09-15
- **Deciders:** Architecture Team

---

## 1. Context & Problem Statement

GH-Bot-Factory is designed to operate storefronts selling digital products supplied by different external vendors and third-party APIs (e.g., Telegram Stars, digital activation codes, gift cards, subscriptions). 

Integrating external suppliers directly into commerce handlers causes high coupling, inconsistent error handling, data leakage across tenants, and risks exposing upstream supplier credentials in application databases or logs. We require an isolated Provider Engine with a uniform protocol, multi-tenant credential isolation, and resilient failover.

---

## 2. Decision Drivers

1. **Protocol Uniformity:** Commerce checkout and fulfillment code must interact with a single abstract supplier protocol regardless of whether the supplier is a mock, a REST API, or an RPC endpoint.
2. **Strict Multi-Tenancy:** Each tenant may have their own upstream accounts, credentials, and profit margins. Upstream accounts must be isolated per tenant.
3. **Zero Secret Exposure:** Upstream API keys, secret hashes, and passwords must never be stored in plaintext in the database or committed to Git.
4. **Resilience & High Availability:** Upstream supplier timeouts or rate limits must not cause immediate checkout failures if an alternate provider exists for the same catalog item.

---

## 3. Key Design Decisions

### A. Provider Protocol Abstraction
We defined a standard Python `Protocol` (`packages.providers.protocol.Provider`) implemented by all provider clients:
- `get_health()`
- `get_balance()`
- `list_products()`
- `get_product()`
- `create_order()`
- `get_order()`

All methods return strongly typed DTOs (`ProviderProductDTO`, `ProviderOrderResponse`, `ProviderBalanceResult`).

### B. Dynamic Client Registry & Secret Resolution
Provider credentials are stored in `ProviderCredential` referencing secret storage keys (`credential_secret_ref`) rather than plaintext secrets. `ProviderClientRegistry` resolves credentials at runtime via the `SecretStorage` protocol and dynamically caches client instances keyed by `(tenant_id, provider_id)`.

### C. Priority Routing & Controlled Failover
The `ProviderRouter` evaluates provider mappings for a given `ProductVariant` ordered by `priority ASC`:
- **Failover on Retryable Errors:** When a provider raises `ProviderTimeoutError` or `ProviderRateLimitError`, the router logs the incident and immediately attempts the next provider in the priority chain.
- **Halt on Non-Retryable Errors:** If a provider encounters a terminal error (such as `ProviderAuthenticationError`), failover is halted immediately. This prevents burning through multiple vendor credentials when an invalid configuration is detected.

---

## 4. Consequences

### Positive:
- New suppliers can be integrated by adding a single client class conforming to `BaseProviderClient`.
- Catalog management and pricing are independent of supplier API structures.
- Multi-provider redundancy protects against supplier outages.

### Negative / Trade-offs:
- Upstream providers must support idempotency keys to ensure that failover attempts do not lead to orphan orders during network partition edge cases.
