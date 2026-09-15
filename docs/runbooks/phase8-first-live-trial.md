# Phase 8 First Live Trial

> **Legacy/advanced path.** The preferred first-run workflow is now `python3 scripts/easy_start.py` followed by the printed `/setup/` URL. See `docs/runbooks/easy-start-web-setup.md`. This document remains for controlled operator/legacy environments.

Use this runbook for the first real Telegram trial after Phase 8.1/8.5. It intentionally bootstraps only one control bot; every additional bot should be created through the Admin Bot Factory wizard.

## 1. Prerequisites

- Ubuntu host with Docker Engine + Compose plugin.
- A public HTTPS endpoint that forwards to the API on `127.0.0.1:8010`.
- One Telegram bot created with BotFather for the control/admin entry point.
- Your own numeric Telegram user ID for the initial OWNER membership.

Do not paste BotFather tokens into chat, Git, database rows, or the Admin UI. Tokens live only in `.env` / the configured secret backend; the UI receives the secret reference name.

## 2. Configure `.env`

```bash
cp .env.example .env
python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Set at least:

```dotenv
APP_ENV=development
POSTGRES_PASSWORD=<strong-random-password>
DATABASE_URL=postgresql+asyncpg://gh_bot_factory:<same-password>@postgres:5432/gh_bot_factory
REDIS_URL=redis://redis:6379/0
JWT_SECRET_KEY=<generated-48-byte-secret>

MINIAPP_PUBLIC_URL=https://YOUR_PUBLIC_HOST/miniapp/
ADMIN_PUBLIC_URL=https://YOUR_PUBLIC_HOST/admin/

CONTROL_BOT_TOKEN=<BotFather token for the bootstrap/control bot>
```

`MINIAPP_PUBLIC_URL` and `ADMIN_PUBLIC_URL` must both be HTTPS when opened by Telegram.

## 3. Start stateful dependencies and migrate

```bash
docker compose up -d postgres redis
docker compose run --rm migrate
```

Expected: Alembic reaches the current repository head with no error.

## 4. Bootstrap the first tenant + OWNER + control bot

```bash
docker compose run --rm api python scripts/bootstrap_first_tenant.py \
  --tenant-slug my-store \
  --tenant-name "My Store" \
  --owner-telegram-id YOUR_NUMERIC_TELEGRAM_ID \
  --owner-username YOUR_TELEGRAM_USERNAME \
  --token-secret-ref CONTROL_BOT_TOKEN \
  --expected-bot-username YOUR_CONTROL_BOT_USERNAME \
  --bot-display-name "My Store Control" \
  --template-key general-commerce
```

The script calls Telegram `getMe` before writing the bot and never prints or persists token material.

## 5. Start the application fleet

```bash
docker compose up -d api worker bot-runtime
curl -fsS http://127.0.0.1:8010/health/ready
```

Expected HTTP result: readiness status 200.

Useful diagnostics:

```bash
docker compose ps
docker compose logs --tail=100 api worker bot-runtime
```

## 6. Open the Admin Web App from Telegram

Open the control bot and send:

```text
/whoami
/admin
```

`/whoami` should report your role as `OWNER`. `/admin` should return an `Open Admin` Web App button. Opening that button supplies signed Telegram `initData`; do not open `/admin/` as a normal browser tab and expect authentication to work.

## 7. Create the first factory-managed bot

Create a second bot with BotFather. Add its token only to `.env`:

```dotenv
TRIAL_STORE_BOT_TOKEN=<second BotFather token>
```

Environment secret references are process environment variables in the current implementation, so recreate the secret-consuming services after adding a new reference:

```bash
docker compose up -d --force-recreate worker bot-runtime
```

In Admin → Bots → **Provision bot**:

1. Select a template such as **Digital Goods** or **Gaming Store**.
2. Continue to Telegram.
3. Set token secret reference to `TRIAL_STORE_BOT_TOKEN`.
4. Set the expected BotFather username.
5. Customize display name, accent, tagline, optional HTTPS logo, support handle/URL, currency, locale, and modules.
6. Submit.

Expected provisioning lifecycle:

```text
PENDING → RUNNING → READY
```

The verified Telegram ID/username should appear in the job and the bot should then appear as ACTIVE in fleet inventory.

## 8. Verify runtime reconciliation

No bot-runtime restart should be needed after the provisioning job reaches `READY`.

Open the newly created bot in Telegram and send `/start`:

- The store button should use the configured wording.
- The welcome message should use the bot configuration.
- Opening the store should show the bot display name, accent color, logo (if configured), and tagline.

Back in Admin, click **Configure** on the bot, change branding, save, and wait one reconciliation interval. Reopen `/start` / the Mini App and confirm the new branding is active.

## 9. Safety/acceptance checks

The first live trial is successful only if all are true:

- [ ] `health/ready` returns 200 before testing.
- [ ] `/whoami` shows OWNER for the bootstrap user.
- [ ] `/admin` opens inside Telegram.
- [ ] Admin never displays the BotFather token or the secret-reference value.
- [ ] Template provisioning reaches READY.
- [ ] Bot appears without manually restarting bot-runtime after READY.
- [ ] Mini App branding matches that bot, not merely the tenant default.
- [ ] A branding edit converges automatically.
- [ ] Disabling the bot in Admin stops it from serving new updates.
- [ ] Re-enabling it resumes after runtime reconciliation.
- [ ] `docker compose logs` contain no token material.

## 10. Before calling the environment production-ready

The live trial is not a substitute for the canonical release gate. Run on the Ubuntu/GitHub environment:

```bash
make release-gate
```

Also preserve the resulting release evidence artifact and complete the staging/restore-drill evidence required by `CURRENT_STATE.md`.

## Credential note

The normal Easy Start/Admin path now writes BotFather tokens to the shared encrypted local vault and does not require recreating worker/bot-runtime containers. Environment-backed secret references remain supported for advanced/legacy deployments. Independent KMS/Vault custody and formal rotation state remain follow-up Phase 8.2B work.
