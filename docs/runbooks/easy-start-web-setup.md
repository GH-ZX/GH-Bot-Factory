# Easy Start — Web-First Factory Setup

The factory owner signs in with a username and password. A Telegram owner bot is optional; no `/admin` command is needed.

## 1. Start the installation

```bash
python3 scripts/easy_start.py
```

The launcher prepares destination-owned `.env` settings, runs database migrations and starts the factory services on port 8010. It prints a local setup URL with a setup code. Keep that URL private; it authorizes first-run initialization.

## 2. Open the printed setup URL

Complete `/setup/`:

1. Factory name and workspace address.
2. Username (5–32 letters/numbers/underscores) and password (at least 12 characters).
3. Setup code from the terminal; optionally confirm the public HTTPS address.

The browser removes the code from its address immediately and does not save credentials in local/session storage. It shows a retry when status is unavailable and disables initialization until the server has a setup code configured.

Submitting creates the factory workspace, password identity, OWNER membership and explicit installation-operator binding in one locked transaction. No Telegram API request or Bot record is created. The installer locks itself after success. Existing installations show a sign-in link instead of an editable form.

## 3. Sign in and review requests

Open `/admin/` and use the username/password. **Sales & leads** uses the same authenticated factory-owner session. Regular tenant OWNER/ADMIN roles cannot access factory-wide sales. Existing installations can still use their environment platform token; migrations do not silently promote an existing tenant owner.

Share `/build/` with prospective customers. Their brief reaches Sales & leads, where the operator reviews scope and prepares a one-time quote. After acceptance, create the customer's workspace. Its initial template and delivery scope come from the accepted quote; feature requests are not automatic integration entitlements.

The factory dashboard does not require a control bot. Customer workspaces retain their separate launch checklist, including connecting the customer's actual bot through verified provisioning. Customer Telegram identity verification, bot launch, supplier/payment credentials and installation packaging are separate from factory access.

## 4. Theme and branding

Edit `apps/shared/static/theme.css`: `--gh-brand` controls the factory accent and derived tints; background, ink, surface and typography are defined beside it. Builder, Admin/sign-in and setup share this file. The unchanged GH Store mark is served from `/shared/gh-store-logo-mark.png`. Redeploy and update the asset cache version when publishing changes. Customer bot branding stays independent.

## 5. Diagnostics

```bash
docker compose ps
curl -fsS http://127.0.0.1:8010/health/ready
```

Zero Telegram bot instances is normal for a web-only factory. Setup unavailable: check migrations and whether `SETUP_CODE` is configured, without printing secrets. An existing username requires its current password; setup must not reset another identity. Owner account recovery is an installation-owner operation, never an unauthenticated web endpoint.

The legacy `/api/v1/setup/initialize` and CLI Telegram bootstrap remain available for older workflows. Real-customer Supabase/VPS testing and production release qualification are separate checks; completing web setup does not establish either.
