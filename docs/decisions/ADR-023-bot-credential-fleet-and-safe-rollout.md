# ADR-023 — Bot Credential Lifecycle, Fleet Observation, and Safe Rollout

- **Status:** Accepted
- **Date:** 2026-09-15

## Context

The first live Easy Start trial proved that first-run provisioning and runtime reconciliation work on Ubuntu/Telegram. The next reliability gap was operational: operators needed to verify/rotate Telegram credentials, distinguish desired state from actual runtime state, constrain fleet growth, and test candidate runtime images on a small subset of bots without SSH-driven restarts.

## Decision

1. PostgreSQL remains desired-state authority for each Bot.
2. Bot credential metadata is persisted separately from secret material: status, version, verified/rotated timestamps, and last normalized error type. Secret values remain only behind `SecretStorage`.
3. Rotation is identity-safe: the new token is verified with Telegram `getMe` and must resolve to the same immutable `telegram_bot_id` before the existing vault value is replaced.
4. `credential_version` and `runtime_revision` participate in the runtime signature so successful rotation/manual restart produces deterministic reconciliation.
5. Redis stores only expiring observed fleet state (`RUNNING`, `FAILED`, `BACKOFF`). Its absence never changes desired state; expired observation becomes `OFFLINE`.
6. Tenant fleet growth is bounded server-side by maximum total bots, enabled bots, and open provisioning jobs. UI/rate limiting is not the authority.
7. Bots belong to `STABLE` or `CANARY`. Runtime processes filter desired bots by `BOT_RUNTIME_RELEASE_CHANNELS`.
8. A rollout overlay permits a candidate bot-runtime image to own CANARY bots while the stable image continues to own STABLE bots. Schema/API changes used during canary windows must remain backward compatible until promotion completes.

## Consequences

- Operators can rotate or verify BotFather credentials without exposing secret references or restarting containers manually.
- Admin fleet status reflects actual runtime heartbeats rather than API-process memory.
- Credential failures have durable, auditable status without persisting token content.
- Canary rollout is real process ownership separation, not a cosmetic tag.
- Redis fleet state is intentionally ephemeral and may show `OFFLINE` briefly during runtime restart/reconciliation.
