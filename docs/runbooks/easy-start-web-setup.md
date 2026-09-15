# Easy Start — First-Run Web Setup

This is the preferred self-hosted first-trial path from Phase 8.2A onward.

## 1. Start everything

From the repository root:

```bash
python3 scripts/easy_start.py
```

The launcher:

- creates `.env` if necessary;
- generates missing PostgreSQL password, JWT signing key, and one-time setup code;
- safely URL-encodes the database connection string;
- builds and starts PostgreSQL, Redis, migrations, API, worker, and bot runtime;
- waits for `/health/ready`;
- prints a local setup URL containing the one-time setup code.

It never prints PostgreSQL/JWT secrets or Telegram bot tokens.

## 2. Open the printed setup URL

Example shape:

```text
http://127.0.0.1:8010/setup/?code=<one-time-code>
```

Complete four sections:

1. store name/slug and your Telegram numeric user ID;
2. BotFather token and optional expected username;
3. template/display name;
4. optional public HTTPS base URL, for example `https://factory.example.com`.

The server calls Telegram `getMe` before writing bot state.

## 3. What success means

After submission the installer creates:

- first Tenant;
- OWNER User/Membership;
- enabled control Bot;
- encrypted vault credential reference;
- optional per-tenant Mini App/Admin public URLs;
- AuditLog installation record;
- singleton install lock.

The bot runtime reconciler should discover the enabled Bot within `BOT_RUNTIME_RECONCILE_SECONDS`, without container restart.

## 4. Test the bot

Open the verified bot and send:

```text
/whoami
/start
```

Expected: `/whoami` reports OWNER and `/start` responds.

If a public HTTPS base URL was configured, also send:

```text
/admin
```

and test the Mini App/Admin Web App buttons.

## 5. Diagnostics

```bash
docker compose ps
docker compose logs --tail=200 api worker bot-runtime
auth_url=http://127.0.0.1:8010/health/ready
curl -fsS "$auth_url"
```

`Initialized 0 Telegram bot instance(s)` is normal only before setup. After successful setup, reconciliation should start the new Bot automatically.

## 6. Existing/advanced installations

The legacy CLI bootstrap remains available for controlled operator workflows, but it is no longer the recommended first-trial path.

For production, still run the canonical release gate and prefer independent external secret custody when available.
