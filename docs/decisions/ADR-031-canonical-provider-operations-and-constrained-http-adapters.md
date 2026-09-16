# ADR-031: Canonical Provider Operations and Constrained HTTP Adapters

- **Status:** Accepted
- **Date:** 2026-09-16
- **Decision owners:** GH-Bot-Factory platform architecture

## Context

GH-Bot-Factory is expected to generate reseller bots that consume many upstream supplier APIs: phone/SMS activation, accounts, gifts, digital products, and generic services. Many suppliers expose Swagger/OpenAPI-shaped REST APIs, but their resource names, status values, authentication schemes, and payload fields differ. Hard-coding vendor names into commerce logic would couple the product to individual suppliers and make tenant-specific provider choice difficult to scale.

A generic integration layer is useful, but accepting arbitrary code, templates, remote OpenAPI references, unrestricted URLs, or redirect-following HTTP clients would turn provider configuration into a remote-code/SSRF/secret-exfiltration boundary.

## Decision

1. Business and bot-template code uses canonical provider contracts, never vendor-specific status strings or endpoint shapes.
2. Provider capabilities are category-aware. A connection only exposes capabilities valid for both its adapter and configured canonical category.
3. Number/SMS activation has an explicit canonical lifecycle and DTO contract. Other reseller categories share canonical order/delivery envelopes and may add category-specific operations as needed.
4. The generic HTTP/OpenAPI adapter is a constrained declarative mapping layer, not a code generator or scripting engine.
5. Generic mappings may describe bounded HTTP method/path/query/body/auth/response selectors and canonical state mappings. They may not execute arbitrary Python, shell, JavaScript, browser code, template expressions, or remote OpenAPI references.
6. Generic HTTP credentials are resolved from `SecretStorage` only at the runtime boundary. Persistable/raw provider responses are recursively redacted against all resolved credential values before they can be stored or surfaced.
7. Redirects are disabled. Literal and DNS-resolved private, loopback, link-local, multicast, reserved, and otherwise non-global targets are rejected by default.
8. In production-like environments, generic HTTP base URLs require HTTPS and an installation-owned host allowlist. Private-network access requires an explicit operator setting and does not weaken the production allowlist requirement.
9. Request timeouts and response-body sizes are bounded. HTTP status and provider status mappings normalize into typed provider errors and canonical order state.
10. OpenAPI/Swagger inspection accepts operator-supplied documents for discovery only. It does not fetch remote specifications, dereference remote `$ref` targets, execute generated clients, or persist a specification as executable authority.
11. Custom reviewed Python adapters remain the escape hatch when a supplier cannot be represented safely by the declarative format.

## Consequences

- New Swagger-like reseller suppliers can often be integrated through configuration while preserving a stable domain API.
- The platform can expose adapter capabilities to a future bot-factory wizard without leaking vendor-specific concepts into the wizard.
- Some unusual APIs require a reviewed custom adapter instead of increasingly powerful generic expressions.
- Operators must intentionally allowlist production supplier hosts, which adds setup friction but sharply reduces credential-exfiltration and SSRF risk.
- Remote specifications remain documentation/input artifacts rather than executable trust anchors.

## Verification

Phase 10.1/10.2 tests cover category-aware capabilities, deterministic number/SMS sandbox lifecycle, OpenAPI inspection, tenant isolation, dynamic credentials/capabilities, redirect refusal, private/reserved DNS rejection, response-size bounds, production host allowlisting, and credential redaction.
