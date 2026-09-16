# Provider Platform Architecture

## Purpose

Phase 10 turns the original supplier abstraction into the integration foundation for multi-API reseller bots. The platform is vendor-neutral by design: number/SMS vendors, account suppliers, gift-code suppliers, digital-product APIs, and service APIs are represented through canonical categories, capabilities, operations, routing policy, normalized offer observations, and asynchronous order state.

Payment providers are intentionally separate and belong to Phase 11 Payments Platform.

## Connection Model

A `Provider` is tenant-owned and represents one configured upstream supplier account. `provider_type` remains the adapter key and `category` declares the canonical business family:

- `NUMBER` — phone numbers/SMS activation providers.
- `ACCOUNT` — account inventory/delivery providers.
- `GIFT` — gift cards/codes/vouchers.
- `DIGITAL_PRODUCT` — subscriptions, keys, top-ups, and generic digital goods.
- `SERVICE` — API-driven services that do not fit an inventory SKU cleanly.
- `OTHER` — controlled escape hatch while a new canonical category is designed.

## Adapter Manifests and Capabilities

`ProviderClientRegistry` owns a safe `ProviderDefinition` manifest for every adapter. A manifest declares supported categories, canonical capabilities, credential requirements, safe configuration fields, driver family, and optional documentation metadata. It contains no tenant credentials.

Capabilities are category-aware. A connection exposes only operations valid for both the selected adapter and the configured canonical category.

## Canonical Operations

Business logic consumes canonical contracts from `packages/providers/contracts.py` rather than vendor payloads or status strings.

Shared concepts include:

- `ProviderOrderState`: `CREATED`, `PENDING`, `PROCESSING`, `WAITING_DELIVERY`, `COMPLETED`, `FAILED`, `CANCELLED`, `EXPIRED`, `REFUNDED`, `UNKNOWN`.
- `ProviderDeliveryArtifact` for text, code, account, phone, SMS, URL, file, or structured delivery.
- canonical account, gift, and digital-service offers/order requests.

Number/SMS providers additionally expose a dedicated activation contract:

```text
list_number_services()
list_number_countries()
list_number_offers()
reserve_number()
get_number_activation()
cancel_number_activation()
finish_number_activation()
```

The Mock/Sandbox adapter implements deterministic activation behavior so bot templates can be developed without live supplier accounts.

## Secret Boundary

Provider credentials support environment references and write-only vault entry. Values are resolved only at the runtime adapter boundary through `SecretStorage`. SQL and Admin read APIs expose configuration status/type, never resolved values or secret references.

Any raw provider payload that can be persisted or surfaced must be recursively redacted against all resolved credential values first. The encrypted local vault remains part of the laptop-to-VPS portable-state contract.

## Constrained Generic HTTP / OpenAPI Driver

`packages/providers/http_generic.py` supports Swagger/OpenAPI-shaped reseller APIs through declarative configuration, not arbitrary executable code.

Allowed mappings cover bounded HTTP method/path/query/body/auth, response selectors, provider status/error normalization, and configured operations. The driver does not execute Python, JavaScript, shell, browser automation, template expressions, generated clients, or remote OpenAPI references.

Security controls are fail-closed:

- redirects disabled;
- bounded timeouts and response sizes;
- credential values resolved only at call time;
- recursive response redaction;
- private/loopback/link-local/multicast/reserved/non-global destinations rejected by default;
- production-like environments require HTTPS and an installation-owned provider-host allowlist;
- OpenAPI inspection accepts operator-supplied documents for discovery only and never fetches remote `$ref` targets.

Custom reviewed Python adapters remain the escape hatch for suppliers that cannot be represented safely.

## Product Mapping and Offer Observations

`ProviderProductMapping` maps tenant catalog identity to upstream product identity. `ProviderOfferSnapshot` stores the latest normalized provider observation for a mapping:

- cost amount/currency;
- availability/stock;
- minimum/maximum quantity;
- observation and expiry timestamps;
- bounded error evidence.

Snapshots are observations, not customer-catalog authority. Fresh unavailable observations can remove a mapping from routing eligibility. Stale observations are advisory and do not block routing. Live refresh is read-only and never creates an upstream order.

## Multi-Provider Routing

`ProviderRoutingPolicy` is tenant-scoped and may target a product default or a specific variant. Supported strategies are:

- `PRIORITY`
- `LOWEST_COST`
- `AVAILABILITY`
- `HEALTHIEST`
- `WEIGHTED`
- `MANUAL`

Variant policy overrides product policy. When no policy exists, legacy routing remains compatible.

Weighted routing uses deterministic hashing from routing/idempotency context and provider identity. `LOWEST_COST` compares only fresh observations in one currency; mixed currency fails closed until explicit FX normalization exists.

Cross-provider failover is deliberately conservative. It is allowed only for retryable errors explicitly classified as pre-order safe (for example known unavailable/rate-limited/insufficient-balance failures). Timeout, transport interruption, or any ambiguity after possible upstream acceptance does not trigger a second supplier purchase; the attempt enters reconciliation/unknown handling.

## Asynchronous Fulfillment Convergence

Provider order acceptance is not the same as delivery. Only canonical `COMPLETED` may mark fulfillment complete. `CREATED`, `PENDING`, `PROCESSING`, and `WAITING_DELIVERY` remain active; `UNKNOWN` remains ambiguous.

The existing durable fulfillment subsystem remains authoritative for attempts, jobs, refunds, retries, operator actions, and accounting. Phase 10 adds pull reconciliation so active upstream orders can converge without webhooks:

```text
CREATE ORDER
    -> PENDING / PROCESSING / WAITING_DELIVERY
    -> durable fulfillment attempt with external_order_id
    -> worker polling reconciliation
    -> COMPLETED
    -> FULFILLED
```

This polling path is first-class for laptop/self-hosted deployments without permanent public ingress.

The current `FulfillmentAttempt` has one durable upstream order identity. If a multi-item order would produce multiple non-terminal upstream orders, the attempt deliberately becomes `UNKNOWN` with evidence rather than being falsely completed or refunded. Per-item upstream correlation/saga is required before broad asynchronous multi-item supplier execution is enabled.

## Persistence

Phase 10 schema additions:

- `a7b8c9d0e1f2` — Provider Platform Core/category and health fields.
- `b8c9d0e1f2a3` — tenant provider routing policies.
- `c9d0e1f2a3b4` — normalized provider offer snapshots.

## Architectural Decisions

- ADR-030 — Provider Platform adapter manifests and vault credentials.
- ADR-031 — Canonical provider operations and constrained HTTP adapters.
- ADR-032 — Deterministic provider routing and ambiguity-safe failover.
- ADR-033 — Provider offer observations and asynchronous order convergence.
