# Laptop-First Hosting and Laptop -> VPS Portability

## Operating model

The default Compose stack already binds the API to `127.0.0.1`, so a laptop can safely act as the host while Telegram-facing Mini App/Admin traffic is published through the operator's chosen HTTPS tunnel/reverse proxy.

The durable state that must survive a host move is:

1. PostgreSQL
2. `/var/lib/ghbf/secret-store` Docker volume

Redis is rebuildable coordination state and is not part of the portable bundle.

## Export

Preferred encrypted export:

```bash
export PORTABLE_GPG_RECIPIENT='<gpg-recipient>'
./scripts/export_portable_state.sh
```

The script briefly stops API/worker/bot-runtime mutation services, exports PostgreSQL and the encrypted vault, writes a manifest/checksums, then restarts only the services that were running before the export.

Plaintext export contains the vault master key and is therefore blocked unless explicitly requested:

```bash
./scripts/export_portable_state.sh --allow-plaintext
```

Treat a plaintext portable archive as a high-value secret.

## Import on a new host

1. Clone/copy the GH Bot Factory code to the destination.
2. Create destination `.env`/Docker runtime configuration (Easy Start may be used to generate host-local credentials).
3. Copy the portable archive to the destination.
4. Import:

```bash
./scripts/import_portable_state.sh --confirm /path/to/ghbf-portable-....tar.gz.gpg
```

The importer validates checksums and bundle format, restores PostgreSQL, restores the encrypted local vault, runs `alembic upgrade head`, and resumes services.

## Destination-owned settings

The portable bundle intentionally does not contain `.env`. Reconfigure these on the destination as appropriate:

- database password / `DATABASE_URL`
- `JWT_SECRET_KEY`
- `PLATFORM_ADMIN_TOKEN`
- `MINIAPP_PUBLIC_URL`
- `ADMIN_PUBLIC_URL`
- proxy/Cloudflare settings
- backup/GPG policy

A new JWT key invalidates old sessions but does not affect tenant/business data or bot/provider credentials.
