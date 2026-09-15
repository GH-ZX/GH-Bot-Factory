# GH-Bot-Factory Current State

> First stop for any human or coding agent resuming work. This file distinguishes implemented behavior from externally executed release evidence.

- **Last updated:** 2026-09-15
- **Current phase:** Phase 8.7 — Bot Factory Release Candidate
- **Implementation status:** First Ubuntu/Telegram Easy Start live trial succeeded: Docker stack became ready and the control bot responded. Credential lifecycle, fleet observation, server-side capacity limits, manual runtime restart, and STABLE/CANARY bot-runtime ownership are now implemented on top of the live-proven Bot Factory. The codebase is at a Bot Factory release-candidate checkpoint; production cutover still requires the canonical external release gate on real PostgreSQL/Docker plus staging recovery evidence.
- **Current migration head:** `b2c3d4e5f6a7`
- **Primary branch:** `main`
- **Canonical repository:** `git@github.com:GH-ZX/GH-Bot-Factory.git`
- **Latest dependency-limited runnable regression:** **198 passed** with PostgreSQL tests and the two direct-Aiogram modules excluded. Current Phase 8 focused suite: **24/24 passed**. A temporary external `aiosqlite` shim was used only for this build session and is not committed.
- **Migration verification in this build session:** clean SQLite upgrade to `b2c3d4e5f6a7` and `alembic check` reporting no new upgrade operations.
- **Canonical release gate:** `make release-gate` with `POSTGRES_TEST_DATABASE_URL` set. PostgreSQL concurrency, Ruff, Aiogram-direct runtime tests, immutable Docker execution, staging recovery, and restore drill remain external evidence requirements.

## Delivered Capabilities

1. Multi-tenant commerce, server-authoritative catalog/checkout, wallet ledger, RBAC, payments, Telegram Stars, reversal/reconciliation, and durable fulfillment.
2. Telegram storefront and tenant Admin console with fulfillment operations, providers, member RBAC, financial resolution, analytics, and audit.
3. Production-readiness layer: immutable containers, observability, health/heartbeats, rate limiting, backups/restore drills, staging failure injection, CI/release evidence, and PostgreSQL concurrency gates.
4. Bot Factory release candidate:
   - one-command Easy Start plus one-time web installer
   - encrypted shared local secret vault with Telegram `getMe` verification
   - durable idempotent bot provisioning and runtime reconciliation
   - versioned templates, per-bot branding, web provisioning wizard, `/admin`, `/whoami`
   - credential status/version/verified/rotated metadata without secret persistence
   - identity-safe BotFather token verification/rotation from Admin
   - Redis-backed expiring observed fleet state separated from PostgreSQL desired state
   - manual runtime restart intent through `runtime_revision`
   - authoritative tenant limits for total bots, enabled bots, and open provisioning jobs
   - STABLE/CANARY release channels with process-level ownership filtering
   - optional `docker-compose.rollout.yml` candidate-runtime overlay
5. Agent-maintainability layer: current-state checkpoint, handoff protocol, changelog, ADRs, roadmap, prompt history, and machine consistency check.

## Current Trust Boundaries

- Tenant/user identity always comes from `AuthenticatedPrincipal`; browser clients cannot choose authoritative tenant/user IDs.
- Money mutates only through ledger services and PostgreSQL serialization/idempotency controls.
- Provider/Telegram secret values exist only at runtime adapter boundaries; DB/API/browser surfaces receive references/status only.
- Bot provisioning and credential rotation treat Telegram `getMe().id` as identity authority.
- Credential rotation verifies the new token before replacing the old vault value; different Telegram identities fail closed.
- PostgreSQL Bot rows are desired state; Redis fleet state is non-authoritative, expiring observation only.
- Bot runtime ownership is filtered by release channel; mixed-version canary windows require backward-compatible schema/API behavior.
- Production/staging controls fail closed on weak JWT, non-PostgreSQL DB, invalid Redis/rate limiting, or insecure Mini App URL.

## Active Documentation Sources

Read in order:

1. `docs/operations/CURRENT_STATE.md`
2. `docs/operations/AGENT_HANDOFF.md`
3. `AGENT_MAP.md`
4. `docs/roadmap.md`
5. `docs/operations/CHANGELOG_AGENT.md`
6. relevant ADRs under `docs/decisions/`
7. `docs/prompts/prompt_history.md`
8. `.agents/skills/gh-bot-factory-core/SKILL.md`

## Verification State

### Runnable/local evidence

- Current Phase 8 focused suites: **24/24 passed**.
- Fast regression excluding PostgreSQL and direct-Aiogram modules: **198/198 passed**.
- `python -m compileall -q apps packages tests migrations`: passed in the final gate.
- Admin/Mini App/Setup JavaScript syntax: passed in the final gate.
- SQLite migration upgrade through `b2c3d4e5f6a7`: passed.
- SQLite `alembic check`: `No new upgrade operations detected.`

### Blocked/canonical evidence

- Real PostgreSQL `make verify-postgres`, including concurrency tests and migration drift: pending target Ubuntu/GitHub environment.
- Ruff: pending declared development environment.
- Direct-Aiogram modules, including runtime reconciliation/release-channel ownership: pending canonical environment.
- Immutable Docker execution, staging failure injection, restore drill, and complete `make release-gate`: pending target Ubuntu/GitHub environment.

## Next Recommended Work

Run the Bot Factory RC on the existing Ubuntu live environment: upgrade migrations, verify the control bot shows RUNNING/VERIFIED in Admin, exercise credential verification/rotation on a disposable bot, create a second bot from the web wizard, and run one CANARY handoff. If those live checks pass, freeze Phase 8 and move to deployment/Phase 9 productization rather than adding more Bot Factory mechanics.
