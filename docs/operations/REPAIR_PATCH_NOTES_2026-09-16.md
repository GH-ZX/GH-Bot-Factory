# Repair Patch Notes — 2026-09-16

> Companion to [REPAIR_REVIEW_2026-09-16.md](REPAIR_REVIEW_2026-09-16.md). This file records what the repair baseline changed, how it was verified, what remains deferred, and how deployment must be authorized. See [ADR-038](../decisions/ADR-038-repair-auth-and-financial-boundaries.md) for the security/accounting decisions.

## Scope

Repair the Admin access failure reported at `http://127.0.0.1:8010/admin/` ("Missing bot_id in admin launch URL") and close the safety/integration defects from the repair review. New Phase 13 feature work remains on hold until the user explicitly requests it.

## Changes

### 1. Admin browser sign-in (P0 access repair)

- New `packages/telegram/admin_login.py`: staff members send `/admin` to their bot in a private chat; the bot issues a 32-character, single-use, five-minute login code stored only as a Redis SHA-256 hash.
- New `POST /api/v1/auth/admin-code` endpoint (`apps/api/v1/auth.py`) exchanges the code for the existing JWT session after re-checking live PostgreSQL authority: active tenant, active user, active staff membership, enabled bot, unchanged `token_version`.
- The Admin web app (`apps/admin/static/`) now offers browser sign-in with the code instead of requiring a Telegram launch URL; invalid-code recovery, sign-out, and mobile layout are handled client-side.
- Codes are never issued in group chats; replay, expiry, malformed input, and revoked/role-changed memberships fail closed. Redis outages return 503 rather than bypassing authentication.

### 2. Per-bot payment policy on every funding route (review P1)

- `apps/api/v1/storefront.py` uses `_legacy_funding_allowed` to disable legacy provider top-up listing/creation for profile-managed bots; method-based routes enforce `_assert_bot_payment_method_allowed`.
- Genuinely legacy bots (no `_business` profile) inherit prior tenant-wide behavior; profile-managed bots with restricted methods can no longer fund through the legacy `/wallet/topups/*` paths.

### 3. Flexible-deposit post-credit reversal monitoring (review P1)

- `packages/payments/flexible_deposits.py` reconciliation no longer short-circuits credited deposits: a later provider `REVERSED` observation is recorded via `_record_credited_reversal`, freezing the affected wallet and opening a `FinancialResolutionCase` instead of silently debiting the customer.
- Settlement remains exactly-once; asset-wallet deposits are tracked through `metadata_json["asset_wallet_id"]` so open cases and freeze/unfreeze cover both wallet kinds.

### 4. Failed portable imports stay offline (review P1)

- `scripts/import_portable_state.sh` restores previously running services only on successful import. Archive validation happens before stopping services; failures after services are stopped leave them offline with a recovery message. Automatic pre-import recovery snapshots and atomic vault replacement remain outstanding.

### 5. Strict profile validation and bot-context integrity (review P2)

- `packages/factory/business_profiles.py` requires real booleans for `allow_flexible_auto_credit`; string `"false"` and other wrong types are rejected rather than coerced truthy. Malformed persisted profiles fail closed.
- A signed bot ID that resolves to a missing/deleted bot no longer falls back to unrestricted legacy defaults; the customer request fails closed.

### 6. Mini App customer payment integration (review P2)

- `apps/miniapp/static/app.js` now lists `/wallet/payment-methods`, supports method-based top-ups, creates and reconciles `/wallet/flexible-deposits`, and displays exact asset/network/credited-value instead of assuming fiat parity.

### 7. Schema drift and checkout race

- Migration `f2a3b4c5d6e7` aligns historical PostgreSQL JSON columns to the existing JSONB models so `alembic check` passes without suppression. No migration was run against the live database.
- Concurrent immutable price-quote insertion in `packages/commerce/economics.py` uses savepoint/re-read so parallel checkouts converge instead of colliding.

### 8. Verification environment repairs

- The virtual environment was missing `pip`; bootstrapped via `ensurepip` so the canonical `pip check` step runs.
- Verification uses the existing `.venv` on PATH; the system Python lacks required application dependencies. No config import repair was needed in the continuation.

## Tests added

- `tests/test_admin_browser_login.py`: single-use codes, authority re-check, replay/tenant-override rejection, group-chat protection, expired/malformed fail-closed.
- `tests/test_admin_browser.cjs`: Playwright browser checks — direct entry, invalid-code recovery, sign-in, sign-out, no token storage, mobile layout; plus Mini App method top-up, manual reference, and flexible-deposit flows.
- `tests/test_portable_import_safety.py`: import failure at each boundary leaves services stopped.
- `tests/postgres/test_economics_concurrency.py`: asset-wallet creation/credit race, hold-vs-debit overspend, duplicate hold capture, flexible-deposit settlement exactly-once for both wallet targets.
- Phase 13 factory tests extended for strict boolean validation and deleted-bot context fail-closed behavior.

## Verification evidence (2026-09-16, this checkout)

Canonical gate: `POSTGRES_TEST_DATABASE_URL=postgresql+asyncpg://ghbf_test:ghbf_test_only@127.0.0.1:55439/ghbf_repair_test PATH="$PWD/.venv/bin:$PATH" make verify` — **exit 0**.

- Ruff: all checks passed (0 findings; the review's 197 findings are resolved).
- Fast suite: **396 passed, 13 deselected** in ~24s.
- `compileall` (apps, packages, scripts, tests, migrations): passed.
- Handoff consistency, secret scan, `git diff --check`, `pip check`: passed.
- Admin/Mini App/Setup JavaScript syntax (`node --check`): passed.
- Alembic upgrade head + `alembic check` on real PostgreSQL: `No new upgrade operations detected.` at head `f2a3b4c5d6e7`.
- PostgreSQL suite: **13 passed**, including all five new economics concurrency races.
- Browser checks (Playwright headless Chromium): Admin direct/invalid-code/login/logout/mobile and Mini App payment-method, manual reference, and flexible-deposit flows passed.

## Blocked / not claimed

- Immutable Docker image rebuild, `make release-gate`, staging failure injection, restore drill, and real provider transactions were **not** run during this repair and remain required before any production claim.
- The live Compose application still runs its previous image; source repairs reach production only after a reviewed rebuild, which requires explicit host-preservation authorization.

## Remaining recommendations (deferred, in order)

1. Commit this repair milestone as a coherent release identity (HEAD `af8fb68` predates Phases 9–13).
2. Build one immutable release revision and run `make release-gate` with PostgreSQL plus staging/restore evidence.
3. Extend the multi-item asynchronous fulfillment decision: restrict launch to single-upstream-order items or implement per-item saga/correlation.
4. Add live FX/oracle policy support before enabling non-PARITY conversion beyond operator-owned fixed rates.
5. After deployment, exercise the Admin browser sign-in and one flexible deposit end-to-end on staging.
