# ADR-032: Deterministic Provider Routing and Ambiguity-Safe Failover

- **Status:** Accepted
- **Date:** 2026-09-16
- **Decision owners:** GH-Bot-Factory platform architecture

## Context

Multi-API reseller bots need to choose among several tenant-configured suppliers by priority, cost, availability, health, explicit preference, or weighted distribution. Retrying a different supplier after an ambiguous timeout is dangerous: the first supplier may already have accepted the purchase even though the response was lost, creating duplicate upstream purchases and incorrect customer accounting.

Provider routing must also remain deterministic enough for idempotent retries and must not silently compare monetary costs expressed in different currencies.

## Decision

1. Tenant-scoped `ProviderRoutingPolicy` records select one strategy for a product or product variant: `PRIORITY`, `LOWEST_COST`, `AVAILABILITY`, `HEALTHIEST`, `WEIGHTED`, or `MANUAL`.
2. Variant-specific policy takes precedence over product-default policy. If no policy exists, legacy routing behavior remains compatible.
3. Weighted routing uses a deterministic hash of the routing/idempotency key and provider identity rather than mutable process-local randomness.
4. Cross-provider failover is allowed only when an error is both retryable and explicitly classified as safe before upstream order acceptance. Examples include known rate-limit, insufficient-balance, or product-unavailable failures.
5. Timeout, transport interruption, or other acceptance ambiguity never causes automatic cross-provider failover. The order enters reconciliation/unknown handling instead.
6. `LOWEST_COST` may compare costs only when all eligible authoritative observations use the same currency. Mixed currencies fail closed until an explicit FX normalization service exists.
7. Manual routing requires an explicitly eligible preferred provider. Policy weights and provider references must belong to the authenticated tenant and mapped product scope.
8. Routing never trusts browser-supplied provider eligibility, cost, health, or availability as authoritative.

## Consequences

- Idempotent retries choose stable supplier ordering.
- The platform trades some automatic recovery for protection against duplicate purchases after ambiguous upstream responses.
- Mixed-currency catalogs cannot use cheapest-provider routing until FX normalization is implemented, but other routing strategies remain available.
- Provider health/availability/cost can evolve independently while policy semantics remain stable.

## Verification

Phase 10.3 tests cover policy CRUD and tenant isolation, deterministic weighted ordering, manual/preferred selection, compatibility with legacy priority routing, safe pre-order fallback, and the no-cross-provider-failover rule for ambiguous timeout/network errors.
