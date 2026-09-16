# ADR-035: Experimental gateway boundary and flexible-deposit policy

- **Status:** Accepted
- **Date:** 2026-09-16

## Context

Some crypto gateways offer fast onboarding and useful APIs but do not provide the same regulatory/governance evidence as regulated payment institutions. GoZaPay additionally offers open-amount stablecoin invoices, which are attractive for Telegram wallet deposits but do not define a fiat conversion contract suitable for the current USD-denominated ledger.

## Decision

Provider integration quality and provider institutional trust are separate dimensions. GH Bot Factory may integrate technically sound but less-established gateways as `CRYPTO_GATEWAY` adapters while labeling them experimental and requiring explicit operator acknowledgement before enablement.

For GoZaPay, fixed invoices are supported with explicit nominal-stablecoin-parity acknowledgement and settlement is delayed until provider `settled`. Flexible/open-amount invoices are not auto-credited into fiat wallets until a future multi-asset wallet/FX contract defines the credited asset, quote source, rounding, expiry, and depeg behavior.

## Consequences

- The integration can be tested and used deliberately without presenting it as a regulatory anchor.
- Fast onboarding does not bypass financial evidence, replay protection, or exact-once settlement.
- Flexible deposits remain a planned feature rather than silently treating one stablecoin as one fiat dollar.
