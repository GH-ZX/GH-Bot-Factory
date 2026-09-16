# ADR-036: Commerce economics, multi-asset balances, and flexible-deposit auto-credit

- **Status:** Accepted
- **Date:** 2026-09-16

## Context

GH Bot Factory is primarily a multi-provider reseller platform. Supplier cost, customer price, reseller/VIP pricing, wallet reservations, profit attribution, supplier balance exposure, and open-amount crypto deposits must therefore share one accounting model rather than being implemented independently by each bot/provider/payment adapter.

Phase 11 intentionally refused to auto-credit GoZaPay open-amount stablecoin deposits into fiat wallets because doing so would silently assume a stablecoin/fiat conversion rule. The user also requires auto-credit to be available as an operator choice.

## Decision

1. Customer sale price is server-authoritative and is frozen in an immutable `CommercePriceQuote` before checkout. Catalog price is only the fallback when no eligible pricing rule plus fresh same-currency supplier observation can produce a safe supplier-based price.
2. Supplier cost observations and customer price are separate facts. Fresh provider observations may drive pricing/routing, but actual fulfilled upstream cost is attributed after provider completion and becomes authoritative for realized gross profit.
3. `ProviderProductMapping.product_id` always references canonical `Product.id`; an optional `product_variant_id` refines the mapping. Variant IDs must never be stored in the product column.
4. Fiat settlement wallets and asset wallets are separate ledgers. Crypto/stablecoin asset balances use explicit `(asset, network)` identity and high-precision decimal accounting.
5. Wallet holds reserve spendable fiat balance without changing booked balance. Capture/release is idempotent and is the only supported reservation lifecycle.
6. Stablecoin-to-fiat conversion is never implicit. A tenant must explicitly create an `FxPolicy` (`PARITY` or `FIXED_RATE`) with an acknowledged rate and optional maximum auto-credit exposure.
7. Flexible/open-amount provider deposits use their own `FlexibleDepositSession`; they are not forced into a fixed-amount `PaymentIntent`.
8. Flexible-deposit auto-credit is optional per payment method and snapshotted when the deposit session is created. Later configuration changes never rewrite the accounting semantics of an already-open session.
9. Auto-credit to an asset wallet credits the verified received asset/net amount exactly once. Auto-credit to a fiat settlement wallet requires a matching enabled FX policy; otherwise the deposit remains `SETTLED_REVIEW` rather than guessing a conversion.
10. Provider balance monitoring is observability only. Low-balance evidence may alert operators but never disables providers or mutates routing automatically.

## Consequences

- GoZaPay open-amount deposits can support optional automatic balance credit without treating USDT/USDC as fiat by accident.
- Tenants can build retail, VIP, and reseller pricing on the same supplier-cost model.
- Realized P&L is based on actual upstream cost when available rather than only estimated mapping cost.
- Provider-balance outages or transient low readings cannot autonomously stop sales.
- A future market-rate FX oracle can be added behind the same FX contract without changing ledger identity or historical quotes.
