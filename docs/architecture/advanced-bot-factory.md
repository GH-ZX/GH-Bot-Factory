# Advanced Bot Factory Architecture

## Goal

Turn the Bot Factory into a business-configuration factory rather than a code generator. A bot selects and constrains capabilities from the shared Commerce, Provider, Payment, and Economics platforms.

## Business profile

Each provisioned bot carries a server-validated `_business` object in `Bot.config`:

```json
{
  "business_type": "NUMBER_SMS",
  "provider_ids": ["..."],
  "payment_method_ids": ["..."],
  "routing_strategy": "AVAILABILITY",
  "preferred_provider_id": null,
  "default_pricing_tier_id": null,
  "allow_flexible_auto_credit": false
}
```

The provisioning job carries the same desired config before Bot creation, so provisioning stays idempotent and portable.

## Runtime enforcement

```text
Telegram / Mini App
        |
        v
 authenticated bot context
        |
        +--> Storefront payment methods ---- profile filter
        |
        +--> Pricing ------------------------ bot default tier
        |
        +--> Checkout ----------------------- immutable price quote
        |
        +--> Fulfillment -------------------- category/provider/routing filter
                                                |
                                                v
                                           Provider Platform
```

The UI never supplies authoritative tenant identity, price, settlement, or provider truth.

## Templates

Advanced templates are vendor-neutral compositions:

- Multi-API Reseller
- Numbers & SMS
- Accounts Store
- Gift Reseller
- Digital Reseller
- Hybrid Store

Templates define business intent and allowed canonical provider categories. Concrete services such as a number API or a Swagger-style gift supplier enter through Provider Platform adapters.

## Compatibility

- no `_business`: legacy tenant/payment behavior and product routing remain authoritative;
- valid `_business` with empty selections: all compatible enabled tenant options;
- malformed `_business`: fail closed;
- disabled/deleted linked resource: runtime filtering removes it from eligibility.

## Launch readiness

The Bot Admin launch-readiness endpoint evaluates:

- commercial access;
- Telegram credential verification;
- runtime state;
- public HTTPS readiness where applicable;
- catalog/variants;
- business-profile provider compatibility;
- fulfillment mappings;
- payment-method availability.

Release qualification still requires the repository-level PostgreSQL/Docker/staging gates in addition to per-bot launch readiness.
