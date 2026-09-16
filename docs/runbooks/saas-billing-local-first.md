# SaaS Billing — Local-First Runbook

Phase 9.2 hosted billing is optional. A laptop-only installation may keep:

```env
BILLING_PROVIDER=disabled
```

Nothing in Bot Factory self-hosted operation requires Stripe.

## Enable Stripe billing

Create recurring Stripe Prices for the plans you want to sell. Configure credentials only in the destination host `.env`/secret-management layer:

```env
BILLING_PROVIDER=stripe
STRIPE_SECRET_KEY=...
STRIPE_WEBHOOK_SECRET=...
BILLING_SUCCESS_URL=https://your-admin-host/admin/?billing=success
BILLING_CANCEL_URL=https://your-admin-host/admin/?billing=cancel
BILLING_PORTAL_RETURN_URL=https://your-admin-host/admin/?billing=return
BILLING_PAST_DUE_GRACE_DAYS=7
```

Production/staging billing redirect URLs must use HTTPS. Never store Stripe secrets in plan metadata or PostgreSQL.

## Register a plan price

List plans and get the plan id:

```bash
python3 scripts/platformctl.py plans
```

Register the provider price:

```bash
python3 scripts/platformctl.py plan-price-add \
  --plan-id <plan-uuid> \
  --provider stripe \
  --external-price-id price_... \
  --currency USD \
  --amount-minor 2900 \
  --interval MONTH
```

Inspect prices:

```bash
python3 scripts/platformctl.py plan-prices --plan-id <plan-uuid>
```

When a provider price should no longer be sold, retire it rather than rewriting its amount/interval:

```bash
python3 scripts/platformctl.py plan-price-retire --price-id <price-uuid>
```

Existing subscriptions can continue to map through a retired price; only new checkout availability is removed.

## Webhook mode (public ingress available)

Point the Stripe webhook endpoint at:

```text
POST /api/v1/billing/webhooks/stripe
```

Subscribe at minimum to:

- `checkout.session.completed`
- `customer.subscription.created`
- `customer.subscription.updated`
- `customer.subscription.deleted`

The endpoint verifies `Stripe-Signature` and stores only normalized billing state/evidence.

## Private laptop mode (no inbound ingress)

Do not expose the laptop solely for billing. Periodically or after a billing action run:

```bash
python3 scripts/platformctl.py billing-reconcile --provider stripe
```

This fetches current provider subscriptions and idempotently converges them locally. Re-running unchanged provider state is safe.

If you later move to a VPS, use the existing portable-state export/import workflow. Billing credentials and public URLs remain destination-owned `.env` values and are intentionally not copied inside the portable database/vault bundle.

## Tenant workflow

Tenant ADMIN/OWNER users see the Billing section under Admin -> Plan.

- No provider-managed subscription: choose an available public price and open provider checkout.
- Existing provider subscription: open **Manage billing** to reach the provider customer portal.
- Checkout/portal browser redirects are not authoritative. Subscription state changes only after a signed webhook or pull reconciliation.

## Past-due behavior

`PAST_DUE` establishes `grace_ends_at` using `BILLING_PAST_DUE_GRACE_DAYS`. Phase 9.2 is observe-only: existing entitlements are not automatically revoked when grace expires. Do not add ad-hoc billing-state checks in product code; Phase 9.3 owns centralized commercial feature enforcement.
