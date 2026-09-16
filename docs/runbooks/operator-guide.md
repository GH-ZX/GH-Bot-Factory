# GH Bot Factory — Operator Guide

This is the practical runbook for a laptop-first GH Bot Factory installation. The same workflow is intentionally portable to a VPS later.

## 1. What runs

The standard Docker Compose stack contains:

- `postgres` — durable business/financial state
- `redis` — rebuildable coordination, heartbeats, rate limits, runtime observations
- `migrate` — one-shot Alembic migration job
- `api` — FastAPI, Admin, Setup, Storefront/Mini App HTTP surfaces
- `worker` — fulfillment, payments, provider/order reconciliation, balances, billing jobs
- `bot-runtime` — Telegram bot processes and runtime reconciliation

Durable portable state is **PostgreSQL + the encrypted secret vault**. Redis can be rebuilt.

## 2. First start on a laptop

Requirements:

- Linux/macOS/WSL2 capable of running Docker
- Docker Engine/Desktop with the Compose plugin
- Python 3.12+ on the host for helper scripts
- a Telegram BotFather token for the first control/store bot

From the project root:

```bash
python3 scripts/easy_start.py
```

Equivalent shortcut:

```bash
make start
```

Easy Start creates `.env` if needed, generates strong local secrets, builds the stack, runs migrations, waits for API readiness, then prints a one-time URL such as:

```text
http://127.0.0.1:8010/setup/?code=<one-time-code>
```

Open that URL in the same laptop browser and complete the installer.

Do **not** share the setup URL or `.env` file.

## 3. First-run setup

The installer asks for:

1. tenant/store identity;
2. OWNER Telegram numeric user ID;
3. BotFather token and optional expected bot username;
4. initial bot/template;
5. optional public HTTPS base URL.

The token is verified with Telegram before it is stored. Secret values go to the encrypted local vault; SQL records keep references/metadata only.

After setup, test the control bot:

```text
/whoami
/start
```

If a public HTTPS Admin/Mini App URL is configured, `/admin` should expose the Admin Web App button.

## 4. Daily commands

Check the host before or after starting:

```bash
make doctor
```

Require a fully running stack:

```bash
make doctor-live
```

Start an already initialized stack:

```bash
make up
```

Status:

```bash
make status
```

Follow application logs:

```bash
make logs
```

Stop application mutation services while keeping PostgreSQL/Redis up:

```bash
make stop
```

A full Compose stop is also safe:

```bash
docker compose stop
```

Avoid `docker compose down -v` unless you deliberately intend to destroy the PostgreSQL/Redis/vault volumes.

## 5. Health checks

Primary readiness check:

```bash
curl -fsS http://127.0.0.1:8010/health/ready
```

Expected HTTP status: `200`.

Also inspect:

```bash
docker compose ps
docker compose logs --tail=200 api worker bot-runtime
```

`make doctor-live` combines the host/config/service/readiness checks without printing secrets.

## 6. Creating a reseller bot

Open Admin → **Bots** → **Create bot**.

The advanced wizard is authoritative server-side and has five stages:

1. **Template**
   - Multi-API Reseller
   - Numbers & SMS
   - Accounts Store
   - Gift Reseller
   - Digital Reseller
   - Hybrid Store
   - legacy/general templates remain supported
2. **Telegram** — bot token/identity and runtime settings
3. **Branding** — texts, accent, support links, menu labels
4. **Business** — allowed providers, payment methods, routing strategy, pricing tier, optional flexible auto-credit
5. **Review** — verify the effective business configuration before provisioning

The browser is not authoritative. Provider/payment/tier IDs are revalidated against the tenant before the profile is saved.

### Empty selections

For a valid profile, leaving provider/payment selections empty means:

- all **compatible enabled** tenant providers; and
- all **enabled** tenant payment methods.

A malformed persisted business profile fails closed instead of expanding access.

### Routing

A new advanced template may select a bot-specific routing strategy. Legacy bots without a business profile continue to inherit existing product-level routing policies.

Supported strategies include:

- PRIORITY
- LOWEST_COST
- AVAILABILITY
- HEALTHIEST
- WEIGHTED
- MANUAL

Automatic provider fallback occurs only for failures known to be safe before possible upstream order acceptance. Ambiguous network/time-out outcomes go to reconciliation instead of risking duplicate purchases.

## 7. Configuring suppliers/APIs

In Admin, configure provider connections before launching a reseller bot.

Recommended sequence:

1. create/enable the provider connection;
2. select its canonical category (`NUMBER`, `ACCOUNT`, `GIFT`, `DIGITAL_PRODUCT`, `SERVICE`, `OTHER`);
3. enter credentials through the write-only secret fields;
4. run **Test connection**;
5. configure product/variant mappings;
6. inspect provider health/offers/balance evidence;
7. select the provider in the bot Business step if that bot should use it.

For Swagger/OpenAPI-style suppliers, prefer the constrained Generic HTTP/OpenAPI adapter when the API fits the canonical contract. It is declarative, blocks redirects, applies response/time limits, redacts credentials, and enforces SSRF/host controls. Use a reviewed custom adapter when the vendor API cannot be mapped safely.

## 8. Products and pricing

Recommended order:

1. create products and variants;
2. map them to suppliers;
3. configure pricing tiers/rules;
4. assign a default pricing tier to the bot if desired;
5. verify catalog price and checkout quote;
6. launch only after the readiness checklist passes.

Checkout freezes one server-authoritative quote. The customer-visible price, wallet debit, and profit attribution use that same quote.

Supplier cost and gross profit are recorded after fulfillment using the actual upstream cost when available.

## 9. Payment methods

Payment methods are tenant-owned; each bot can expose only a subset.

Implemented payment architecture includes:

- manual payments/admin review;
- native/self-custody chain verification foundation;
- NOWPayments;
- Triple-A;
- Bybit Pay;
- Binance Pay polling integration;
- GoZaPay, treated as an optional/experimental crypto gateway;
- Telegram Stars from earlier phases.

Payment callbacks never directly mutate wallet balance. Evidence is verified/reconciled and then passes through the exactly-once ledger settlement gate.

### GoZaPay flexible deposits

Open-amount/flexible deposit auto-credit is **optional per payment method and per bot**.

- auto-credit off → settled payment goes to review;
- exact-asset auto-credit → received asset can credit the matching asset wallet exactly once;
- fiat-wallet auto-credit → requires an explicit FX/PARITY policy; there is no hidden `1 stablecoin = 1 fiat` assumption.

The policy is snapshotted when the deposit session is created, so later Admin setting changes do not rewrite the meaning of an already-open session.

## 10. Laptop networking

The default API port binds to loopback:

```text
127.0.0.1:8010
```

This is suitable for laptop operation.

Telegram polling, provider polling, payment reconciliation, and async fulfillment convergence do not require permanent public ingress.

A **public HTTPS URL is still required** for Telegram Web Apps/Mini Apps and any provider that requires an inbound webhook URL. Use a trusted reverse proxy/tunnel when those surfaces are needed and configure:

```text
MINIAPP_PUBLIC_URL=https://...
ADMIN_PUBLIC_URL=https://...
```

Do not expose PostgreSQL or Redis publicly.

## 11. Updating the application

Before an upgrade, create a backup/portable export.

Then update code and run:

```bash
make upgrade
```

This runs the migration job and rebuilds/restarts application services.

Afterward:

```bash
make doctor-live
curl -fsS http://127.0.0.1:8010/health/ready
```

For a production release, also run the canonical release gate described in `docs/runbooks/release-gate.md`.

## 12. Backup and portable export

Preferred laptop/VPS portable export is GPG-encrypted:

```bash
export PORTABLE_GPG_RECIPIENT='<your-gpg-recipient>'
make portable-export
```

The bundle contains:

- PostgreSQL dump
- encrypted secret vault, including the key material required to decrypt stored secrets
- manifest/checksums/migration metadata

It intentionally excludes `.env` and Redis.

A plaintext portable export is high-value secret material and is blocked unless explicitly allowed.

## 13. Move from laptop to VPS

On the destination VPS:

1. install Docker + Compose and Python 3.12+;
2. copy/clone the same GH Bot Factory release;
3. create destination-owned `.env` (Easy Start can initialize one);
4. set destination public URLs/proxy settings;
5. copy the encrypted portable bundle;
6. import:

```bash
./scripts/import_portable_state.sh --confirm /path/to/ghbf-portable-....tar.gz.gpg
```

Then:

```bash
make doctor-live
```

A new destination JWT key signs new sessions; business data and encrypted provider/bot credentials come from the imported portable state.

## 14. Troubleshooting

### API is not ready

```bash
docker compose ps
docker compose logs --tail=250 migrate postgres redis api
```

### Worker/provider/payment reconciliation issue

```bash
docker compose logs --tail=300 worker
```

Check Admin Financial Center, payment operations health, provider health, and reconciliation evidence before manually changing any money/order state.

### Bot does not start

```bash
docker compose logs --tail=300 bot-runtime api
```

Then inspect Admin → Bots:

- credential verification status;
- enabled state;
- release channel;
- launch readiness;
- runtime observation.

### Supplier order is `UNKNOWN`

Do not blindly retry with a different supplier. `UNKNOWN` intentionally protects against duplicate purchase when the first upstream provider may already have accepted the order. Use reconciliation/operator evidence.

## 15. Pre-launch checklist

Before taking real customer money:

```bash
make doctor-live
make verify-fast
```

On a proper PostgreSQL/Docker release host also run:

```bash
export POSTGRES_TEST_DATABASE_URL='postgresql+asyncpg://...'
make verify-postgres
make release-gate
```

Also complete:

- staging E2E/failure-injection run;
- encrypted backup + restore drill;
- Telegram direct-runtime tests;
- payment/provider sandbox tests for the integrations you will actually enable;
- smallest-possible live transaction test for each enabled real payment/provider integration;
- launch-readiness checklist for each production bot.

The codebase is designed to fail closed when financial/provider truth is ambiguous. Do not bypass an `UNKNOWN`, review, or reconciliation state just to make a transaction appear complete.
