# ADR-037: Advanced Bot Business Profiles and Factory Wizard

- Status: Accepted
- Date: 2026-09-16

## Context

GH Bot Factory now has shared provider, payment, pricing, wallet, and fulfillment platforms. A reseller bot must be able to select a tenant-owned subset of those capabilities without generating vendor-specific bot code or weakening tenant isolation.

The selection must exist before the durable Bot row is created, because provisioning is asynchronous. It must also remain portable with the bot and must not require a new relational schema merely to represent wizard state.

## Decision

1. Store the authoritative per-bot business profile in the versioned `Bot.config` snapshot under `_business`. The same snapshot is carried by the provisioning job before Bot creation.
2. Validate every referenced provider, payment method, and pricing tier server-side against the authenticated tenant before persisting the profile.
3. Enforce the profile at runtime:
   - storefront payment-method exposure;
   - flexible auto-credit permission;
   - default pricing tier selection;
   - provider category/provider selection and bot-specific routing override during fulfillment.
4. Empty provider/payment selections in a valid profile mean all compatible/enabled tenant options. A malformed persisted profile fails closed.
5. Legacy bots without `_business` retain backward compatibility: provider/payment access follows tenant defaults, flexible auto-credit follows payment-method policy, and product-level routing policy is inherited rather than silently replaced.
6. Advanced templates describe business intent (`RESELLER`, `NUMBER_SMS`, `ACCOUNT`, `GIFT_CARD`, `DIGITAL_PRODUCT`, `HYBRID`) and canonical provider categories. They do not name concrete vendors.
7. The browser wizard is a presentation layer only. Tampering with submitted IDs or categories cannot cross the server-side tenant/category boundary.

## Consequences

- One commerce/runtime codebase serves many bot business types.
- Provider/payment adapters stay optional and vendor-neutral.
- A bot can be moved laptop -> VPS with its business profile in the existing durable database snapshot.
- No Phase 13 migration is required; migration head remains `e1f2a3b4c5d6`.
- Deleting/disabling a referenced tenant resource removes it from runtime eligibility; the saved profile never grants authority by itself.
