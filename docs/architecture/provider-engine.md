# Provider Engine Architecture

## 1. Overview & Objectives

The **Provider Engine** decouples the Commerce Core and Telegram presentation layers from external supplier and vendor APIs. A tenant (storefront) can sell digital goods, mobile top-ups, game cards, or subscription vouchers sourced from multiple external providers without exposing external API quirks, schemas, or authentication mechanics to internal domain models.

### Core Architectural Invariants:
1. **Tenant Isolation:** Providers, credentials, and product mappings are strictly scoped by `tenant_id`. Tenant A cannot access or route through Tenant B's upstream accounts.
2. **Secret Safety:** Credentials store secret references (`credential_secret_ref`) rather than plaintext API keys or tokens. Real secrets are resolved at runtime via the `SecretStorage` abstraction.
3. **Protocol Uniformity:** Every upstream provider implements the canonical `Provider` Python Protocol (`health_check()`, `get_balance()`, `list_products()`, `get_product()`, `create_order()`, `get_order()`). Phase 10 adapter manifests additionally declare category/capability compatibility.
4. **Resilient Failover:** Orders routed across multiple configured providers automatically fail over when encountering transient network or rate limit errors, but immediately halt on non-retryable errors (such as authentication failures).

---

## 2. Domain Data Models

```
┌────────────────────────────────┐
│             Tenant             │
└───────────────┬────────────────┘
                │ 1..*
    ┌───────────┴───────────┐
    ▼                       ▼
┌──────────────┐    ┌───────────────────────────┐
│   Provider   │    │      ProductVariant       │
└───────┬──────┘    └─────────────┬─────────────┘
        │ 1..*                    │ 1..*
        │           ┌─────────────┘
        ▼           ▼
┌───────────────────────────────┐
│   ProviderProductMapping      │
│ - provider_id                 │
│ - product_id (variant)        │
│ - external_product_id         │
│ - priority (asc)              │
│ - cost_price (for margin calc)│
│ - is_active                   │
└───────────────────────────────┘
```

### Key Models:
- **`Provider`**: Identifies a configured external provider connection within a tenant (`tenant_id`, `name`, `slug`, adapter key in `provider_type`, canonical `category`, enablement/priority, health evidence, and safe metadata).
- **`ProviderCredential`**: Secure metadata referencing provider credentials (`credential_type`, `secret_ref`). Plaintext values are resolved only through `SecretStorage`; Phase 10 can write them directly to the encrypted local vault without persisting them in SQL.
- **`ProviderProductMapping`**: Maps an internal catalog `ProductVariant` to an upstream `external_product_id`. Supports ordering by `priority` (lower numbers tried first), tracking supplier `cost_price`, and selective toggling via `is_active`.

---

## 3. Provider Protocol & Client Registry

All supplier integrations inherit from `BaseProviderClient` and implement the `Provider` protocol:

```python
class Provider(Protocol):
    async def health_check(self) -> ProviderHealthResult: ...
    async def get_balance(self) -> ProviderBalanceResult: ...
    async def list_products(self) -> list[ProviderProductDTO]: ...
    async def get_product(self, external_id: str) -> ProviderProductDTO: ...
    async def create_order(self, request: ProviderOrderRequest) -> ProviderOrderResponse: ...
    async def get_order(self, external_order_id: str) -> ProviderOrderCheckResponse: ...
```

### Provider Client Registry
The `ProviderClientRegistry` owns adapter factories plus public `ProviderDefinition` manifests. `ProviderRouter` resolves a tenant provider record and its credential references, validates the record category against the adapter manifest, then asks the registry for the matching client (`MockProvider`, `ExampleDigitalCodesProvider`, future vendor drivers, etc.). Test-only singleton injection remains supported. See `docs/architecture/provider-platform.md`.

---

## 4. Routing & Failover Mechanics

The `ProviderRouter` handles supplier selection and failover dispatch:

```
Order Request
     │
     ▼
Find Active Mappings for Variant (ordered by priority ASC)
     │
     ▼
┌────────────────────────────────────────┐
│ For each candidate Provider:          │
│ 1. Verify Provider is enabled          │
│ 2. Instantiate client via Registry     │
│ 3. Execute `create_order(request)`     │
│                                        │
│ ──> Success: Return response           │
│ ──> Retryable Error (Timeout / 429):   │
│     Log warning, continue to next      │
│ ──> Non-Retryable Error (Auth / 400):  │
│     Abort immediately, raise exception │
└────────────────────────────────────────┘
     │
     ▼ (All failed)
Raise `ProviderError("All providers exhausted")`
```

### Error Classification:
- **Retryable / Failover Errors:**
  - `ProviderTimeoutError`: Connection or response read timeout.
  - `ProviderRateLimitError`: Upstream rate limit or temporary 503 throttling.
- **Non-Retryable / Terminal Errors:**
  - `ProviderAuthenticationError`: Invalid API key or expired token. Failing over cannot resolve credential errors; immediate administrator alerting is required.
  - `ProviderInsufficientBalanceError`: Upstream supplier account depleted.
  - `ProviderProductUnavailableError`: SKU out of stock or retired upstream.
