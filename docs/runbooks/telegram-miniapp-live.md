# Telegram Mini App — Live Launch Runbook

This runbook takes the existing multi-tenant backend from a local checkout implementation to a real Telegram launch path.

## 1. Public HTTPS endpoint

Telegram `WebAppInfo` requires an HTTPS URL. Publish the API service through a TLS reverse proxy or tunnel and route the public hostname to `api:8010` (or `127.0.0.1:8010` outside Docker).

The configured Mini App URL must point at the mounted static client:

```env
MINIAPP_PUBLIC_URL=https://shop.example.com/miniapp/
MINIAPP_MENU_TEXT=Open Store
```

The runtime appends the internal bot UUID as `bot_id`. Existing query parameters are preserved, and any client-supplied `bot_id` in the configured URL is replaced.

Do not expose port 8010 directly as the public production endpoint. Terminate TLS in front of it.

## 2. Secrets

Generate an application JWT signing key and keep bot tokens out of the database:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Example `.env` values:

```env
JWT_SECRET_KEY=<generated-value>
TELEGRAM_BOT_TOKEN=<token-from-BotFather>
POSTGRES_PASSWORD=<strong-password>
DATABASE_URL=postgresql+asyncpg://gh_bot_factory:<same-password>@postgres:5432/gh_bot_factory
```

A `Bot` row stores only `token_secret_ref=TELEGRAM_BOT_TOKEN`; the runtime resolves the token from the environment.

## 3. Start infrastructure and migrate

```bash
docker compose up -d postgres redis
docker compose run --rm migrate
docker compose up -d api worker
```

Confirm the API locally:

```bash
curl http://127.0.0.1:8010/health
```

Confirm the public Mini App shell:

```bash
curl -I https://shop.example.com/miniapp/
```

## 4. Register a bot and tenant

The registration command is idempotent for a bot already assigned to the same tenant. It refuses cross-tenant reassignment.

```bash
docker compose run --rm api python scripts/register_telegram_bot.py \
  --tenant-slug demo-store \
  --tenant-name "Demo Store" \
  --username my_store_bot \
  --display-name "Demo Store"
```

If `TELEGRAM_BOT_TOKEN` is configured, the numeric Telegram bot id is derived from the token prefix. Otherwise pass `--telegram-bot-id` explicitly.

The command prints the internal `bot_id` UUID used in Mini App routing. It never prints the bot token.

## 5. Start the Telegram runtime

```bash
docker compose up -d bot-runtime
```

At startup the runtime:

1. loads all enabled `Bot` rows;
2. resolves each token by secret reference;
3. configures the persistent Telegram menu button to open the tenant-safe Mini App URL;
4. starts isolated aiogram polling tasks for each bot.

Inspect logs:

```bash
docker compose logs -f bot-runtime
```

## 6. Telegram user flow

In a private chat with the bot:

1. send `/start`;
2. press **Open Store**;
3. Telegram opens the HTTPS Mini App and provides signed `initData`;
4. the browser sends raw `initData` plus the non-secret internal `bot_id` to the backend;
5. the backend verifies the signature with the authoritative bot token, resolves the tenant, and issues a short-lived JWT;
6. catalog, wallet, order and checkout requests use that JWT;
7. checkout writes the paid order, wallet debit and durable fulfillment job atomically;
8. the worker executes fulfillment outside the HTTP request.

The client never supplies an authoritative tenant id, user id, price or wallet balance.

## 7. Optional BotFather profile launch button

The code configures the in-chat menu button automatically and adds Web App buttons to `/start` and `/menu`. A prominent main Mini App launch button on the bot profile can additionally be configured in BotFather using the same public HTTPS Mini App URL.

## 8. Smoke-test checklist

- `/health` returns `{"status":"ok"}`.
- `/miniapp/` is reachable over public HTTPS.
- `/start` shows **Open Store**.
- Telegram's persistent menu button opens the same Mini App.
- The Mini App does not work when opened directly in a normal browser without Telegram `initData`.
- A valid Telegram launch authenticates and loads only the owning tenant's catalog.
- A duplicate checkout submission returns the same order and debits the wallet once.
- A second bot owned by the same tenant maps the same Telegram customer to the same domain `User`.
- Worker logs show the durable fulfillment job leaving `QUEUED`/`RUNNING` according to provider behavior.
