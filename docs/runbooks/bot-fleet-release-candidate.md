# Bot Fleet Release Candidate Runbook

## After Easy Start succeeds

1. Open Admin -> Bots.
2. Confirm the control bot shows `RUNNING` and credential `VERIFIED`.
3. Use **Verify credential**. No token should be requested or displayed.
4. For a real token rotation, generate a replacement in BotFather, choose **Rotate token**, paste it once, and confirm runtime returns to `RUNNING`.
5. Use **Restart runtime** to test reconciliation without Docker/SSH intervention.
6. Create a second bot from the web wizard and confirm it reaches READY and RUNNING.

## Canary test

1. Build/tag a candidate image.
2. Start the rollout overlay with `GHBF_CANARY_IMAGE_TAG`.
3. Move only the test bot to CANARY from Admin.
4. Confirm STABLE control bot remains running and CANARY bot is observed running by the candidate runtime.
5. Exercise `/start`, `/whoami`, `/admin`, Mini App, catalog, and checkout on the CANARY bot.
6. On failure, move it back to STABLE. Desired-state reconciliation performs the handoff.

## Capacity controls

Defaults:

- total bots/tenant: 50
- enabled bots/tenant: 20
- open provisioning jobs/tenant: 10

Override only through deployment configuration. API enforcement is authoritative.
