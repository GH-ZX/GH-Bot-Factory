# ADR-030: Provider Platform Adapter Manifests and Write-Only Vault Credentials

- **Status:** Accepted
- **Date:** 2026-09-16

## Context

The primary product direction includes multi-API reseller bots for phone numbers/SMS activation, accounts, gift codes, digital products, and services. Many upstream vendors expose similar REST/Swagger-style APIs, but their authentication, catalog schemas, order states, and delivery semantics differ. Hard-coding one bot or commerce path per vendor would couple business logic to vendor quirks and make dozens of integrations unmaintainable.

The older provider layer already has tenant-scoped provider records, secret references, product mappings, routing, and failover. Phase 10 needs to preserve that compatibility while turning it into an explicit integration platform.

## Decision

1. A tenant `Provider` record represents a configured provider connection. The existing `provider_type` field remains the adapter key for backward compatibility.
2. Every connection has a canonical business category: `NUMBER`, `ACCOUNT`, `GIFT`, `DIGITAL_PRODUCT`, `SERVICE`, or `OTHER`.
3. Adapter code registers a public `ProviderDefinition` manifest declaring supported categories, canonical capabilities, credential requirements, driver family, and safe operator metadata.
4. Commerce/domain code consumes canonical capabilities and provider interfaces. It does not branch on vendor names such as 5sim or any future supplier.
5. Tenant Admin may submit a credential value through a write-only endpoint. The API immediately writes the value to `SecretStorage` and persists only a deterministic secret reference. Secret values and secret references are never returned in provider read responses.
6. Environment secret references remain supported for operators who prefer environment-owned credentials.
7. Connection tests validate adapter/category compatibility and required credentials, use bounded timeouts, persist sanitized health evidence, and never expose upstream exception bodies or secret material.
8. Existing product mappings and resilient routing remain valid. New category-specific contracts, OpenAPI mapping, richer routing policies, aggregation, and asynchronous order lifecycle are delivered in later Phase 10 milestones.
9. The local encrypted vault is part of the portable state contract, so write-only credentials remain portable from laptop to VPS without re-entering every API key.

## Consequences

- New vendors become adapters/drivers instead of forks of bot/business logic.
- A future constrained OpenAPI/Swagger mapping layer can sit behind the same manifest and capability boundary.
- Tenants can configure providers from the Admin UI without plaintext credentials entering PostgreSQL.
- The platform can support many similar reseller APIs while keeping category/order semantics canonical and testable.
