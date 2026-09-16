# Commerce Economics Architecture

Phase 12 adds the accounting and pricing layer required for multi-API reseller bots. It does not replace the existing payment ledger, provider platform, catalog, or fulfillment engine; it connects them through durable economic records.

## Authoritative flow

```text
Provider offer observation
        |
        v
Pricing rules + customer tier
        |
        v
Immutable CommercePriceQuote
        |
        v
Checkout / wallet debit
        |
        v
OrderItemEconomics (estimated cost)
        |
        v
Provider fulfillment
        |
        v
Actual supplier cost + realized gross profit
```

The price shown to the customer, the price debited at checkout, and the sale amount used for P&L come from the same server-side quote. Client price fields are never authoritative.

## Pricing tiers and rules

A tenant may define customer/reseller tiers and rules scoped to global, category, product, or variant level. Rules support fixed, percentage, or mixed markup plus minimum margin and rounding increments. Tier assignment is tenant-scoped. At most one active default pricing tier exists per tenant; the database enforces the invariant.

Supplier-cost pricing is allowed only when a fresh eligible supplier offer exists in the same currency as the sale. Cross-currency pricing remains fail-closed unless an explicit future normalization layer is used.

## Supplier mapping identity

`ProviderProductMapping.product_id` references `Product.id`. `product_variant_id` is an optional refinement. Fulfillment resolves the canonical product from each order-item variant and routes using both identities. This prevents the historical SQLite-friendly mistake of placing a variant UUID in the product column and preserves PostgreSQL foreign-key correctness.

## Wallet holds

`WalletHold` reserves spendable fiat balance without mutating the booked wallet balance. Available balance is booked balance minus active holds. Capture creates one idempotent debit; release restores spendability without a compensating ledger mutation.

## Asset wallets and FX policy

`AssetWallet` stores explicit asset/network balances at high precision. Asset wallet identity is `(tenant, user, asset, network)` and every mutation creates an idempotent immutable `AssetLedgerTransaction`.

There is no implicit stablecoin parity. Fiat auto-credit requires an enabled tenant-owned `FxPolicy` with a declared source asset/network, destination currency, rate, mode (`PARITY` or `FIXED_RATE`), acknowledgement timestamp, and optional maximum auto-credit amount.

## Flexible/open-amount deposits

Open-amount deposits use `FlexibleDepositSession`, because the received amount is not known when the session is created. The session snapshots the payment method's auto-credit policy.

```text
Flexible invoice
   -> provider detects received amount
   -> reconciliation/polling
   -> settled evidence
   -> auto-credit policy snapshot
      -> ASSET_WALLET: exact verified asset credit
      -> SETTLEMENT_WALLET: explicit FX policy required
      -> disabled/no policy: SETTLED_REVIEW
```

The worker polls open sessions, so laptop/self-hosted installations do not require permanent public ingress. Auto-credit is optional per payment method. Once credited, provider reversal evidence is escalated for financial resolution rather than silently debiting the customer.

## Provider balance observability

Providers exposing the canonical `BALANCE` capability are polled by `ProviderBalanceMonitorWorker`. Snapshots include balance, currency, configured low-balance threshold, observation time, and bounded error evidence. Monitoring is read-only: it never disables the provider or changes routing.

## Trust boundaries

- Prices and conversion policies are server-authoritative.
- Provider observations are evidence, not customer-supplied truth.
- Wallet and asset-ledger mutations are exactly-once service operations.
- Flexible-deposit policy is snapshotted at creation time.
- No payment webhook directly mutates a wallet.
- No low-balance observation directly changes provider availability.
