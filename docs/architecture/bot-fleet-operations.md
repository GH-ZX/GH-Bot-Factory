# Bot Fleet Operations

## Authorities

- **PostgreSQL `bots` row:** desired state, credential metadata, release channel, runtime revision.
- **Encrypted SecretStorage:** Telegram token value.
- **Telegram `getMe`:** immutable bot identity authority.
- **Redis fleet key:** ephemeral observed runtime status only.

## Credential Rotation

```text
Admin pastes new token
  -> API verifies getMe(new token)
  -> telegram_bot_id must equal stored bot identity
  -> write encrypted vault value
  -> credential_version += 1
  -> runtime reconciliation detects signature change
  -> bot polling restarts with new token
```

A failed identity check leaves the old secret untouched.

## Fleet Observation

Each live bot runtime publishes an expiring Redis record. Admin combines that observation with PostgreSQL desired state:

- enabled + RUNNING: healthy
- enabled + missing observation: OFFLINE/unknown runtime
- enabled + BACKOFF/FAILED: operator attention
- disabled + no observation: expected DISABLED

## Safe Rollout

Default single-node development runtime owns both channels:

```env
BOT_RUNTIME_RELEASE_CHANNELS=STABLE,CANARY
```

For a candidate runtime image:

```bash
GHBF_CANARY_IMAGE_TAG=<candidate-tag> \
  docker compose -f docker-compose.yml -f docker-compose.rollout.yml \
  up -d bot-runtime bot-runtime-canary
```

The base runtime is overridden to `STABLE`; the candidate service owns only `CANARY`. Move one bot to CANARY from Admin, observe it, then promote back to STABLE or expand the canary set.

Never deploy a migration that makes the stable image unable to read/write the shared schema during a mixed-version canary window.
