# Bot Factory Provisioning Architecture

## Purpose

Phase 8.0/8.4 converts bot creation from a manual database/runtime procedure into a durable desired-state workflow.

```text
Admin (ADMIN/OWNER)
        |
        | token_secret_ref + expected username + safe config
        v
POST /api/v1/admin/bots/provision
        |
        | durable + tenant idempotency fingerprint
        v
BotProvisioningJob (PostgreSQL)
        |
        v
Provisioning Worker
        |
        +--> SecretStorage.get_secret(ref)  [ephemeral plaintext only]
        |
        +--> Telegram getMe
        |       |
        |       +--> telegram_bot_id is global identity authority
        |
        +--> ownership / expected-username checks
        |
        v
Bot desired-state row
        |
        v
Bot Runtime Reconciliation Loop
        |
        +--> start newly enabled bot
        +--> restart changed/crashed bot
        +--> stop disabled/deleted bot
```

## Durable Job Contract

`BotProvisioningJob` stores intent and evidence but never secret values. A worker claim writes a lease before external I/O. If the worker dies, an expired `RUNNING` lease becomes `RETRY` and can be reclaimed.

Terminal configuration/identity failures become `FAILED`. Transient Telegram/network failures become `RETRY` until `max_attempts` is reached.

The same tenant/idempotency key may be replayed only when its request fingerprint is identical. Reusing the key for a different bot/config is rejected.

## Telegram Identity

- `telegram_bot_id`: authoritative and globally unique in the platform.
- `username`: mutable Telegram metadata and optional operator assertion.
- `token_secret_ref`: secret pointer only; never returned to browser clients.
- `display_name`/config: tenant desired state.

If two workers race to create the same Telegram identity, the database uniqueness constraint is authoritative. The loser transitions to a retry path and then observes the owner that won the race.

## Runtime Reconciliation

The bot runtime no longer assumes its startup snapshot is permanent. It periodically loads enabled/non-deleted `Bot` rows and compares a desired-state signature with active processes.

- missing active instance -> initialize/start
- changed desired signature -> stop/reinitialize/start
- completed/crashed polling task -> restart
- disabled/deleted desired row -> stop
- unchanged -> no action

This loop is process-local orchestration; PostgreSQL remains the desired-state source of truth.

## Security Boundary

Admin users submit only an environment/vault key identifier. Config recursively rejects secret-like keys and Telegram-token-shaped values. API responses expose only `token_configured: true/false`. Runtime/provisioning logs persist normalized error type/codes only.
