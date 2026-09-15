# Bot Templates & Branding Architecture

## Normal provisioning path

```text
Admin Wizard
  → GET /admin/bots/templates
  → choose template@version + safe overrides
  → POST /admin/bots/provision
  → server build_template_config()
  → durable BotProvisioningJob.desired_config
  → Telegram getMe verification
  → Bot.config persisted with _factory provenance
  → runtime reconciliation
  → Telegram + Mini App consume per-bot config
```

## Trust boundary

The browser may propose a template and branding values, but it does not construct authoritative config. Server validation owns the final config. Secret material is forbidden from config and credential references remain outside browser responses.

Mini App authentication signs `bot_id` into the API JWT only after Telegram initData has been verified against the corresponding bot token. Storefront bootstrap uses this signed bot context to merge public per-bot branding over public tenant defaults.

## Template provenance

Every template-created config includes:

```json
{
  "_factory": {
    "template_key": "general-commerce",
    "template_version": 1
  }
}
```

Templates are immutable for a given version. Change defaults by introducing a new version rather than mutating the meaning of an existing version.

## Public branding surface

Current template branding supports:

- welcome text
- storefront tagline / description
- accent color
- HTTPS logo URL
- Telegram support handle
- HTTPS support URL
- Telegram persistent menu text
- `/start` store button text
- locale / currency
- enabled modules (`catalog`, `orders`, `account`)

Storefront bootstrap exposes only allow-listed public branding values.
