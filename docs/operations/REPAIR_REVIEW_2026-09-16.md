# Phase 13 Repair Review — 2026-09-16

## Scope and decision

Reviewed the current working tree, project constitution, handoff, prompt history, roadmap, verification scripts, and selected payment, bot-profile, portability, and customer-client paths. This is a targeted review, not an exhaustive security audit. No application repairs or live infrastructure changes were made.

The next update should close safety and integration gaps and qualify the existing feature scope for release. Phase 13 is implemented, but its feature-complete declaration is stronger than the customer integration and release evidence currently support.

## Prioritized repairs

### P1 — Enforce bot payment restrictions on every customer funding route

`apps/api/v1/storefront.py:1086` lists tenant payment providers through the legacy `/wallet/topups/options` path; `create_wallet_topup` at line 1108 creates by provider name without consulting `_assert_bot_payment_method_allowed`. The newer method-based routes enforce the profile, but the legacy routes remain reachable with the same customer token. `apps/miniapp/static/app.js:226` and `:733` still use these legacy routes.

Impact: selecting a restricted payment-method list in the wizard does not constrain the existing Mini App funding flow. This is a per-bot policy bypass within the tenant, not evidence of cross-tenant access.

Repair: centralize authoritative funding eligibility and apply it to legacy and method-based creation/listing. Preserve inheritance only for genuinely legacy bots. Add API coverage proving an excluded provider cannot be listed or used via the legacy route.

### P1 — Observe flexible-deposit reversals after credit

`packages/payments/flexible_deposits.py:269` returns immediately for `CREDITED`; the periodic query at line 298 also excludes credited deposits. Consequently the later `REVERSED` handling at line 281 cannot observe a normal credited-to-reversed transition through this reconciliation path.

Evidence: a direct probe using a credited record returned without calling the provider boundary.

Repair: introduce bounded post-credit reconciliation or another authenticated reversal-ingestion path, with durable financial-resolution evidence and an explicit freeze/recovery policy. Keep settlement exactly-once. Test settlement, later reversal, duplicate reversal, and worker restart; do not silently debit a customer.

### P1 — Make failed portable imports remain offline

`scripts/import_portable_state.sh:85` restarts previously running services on every exit, including failure. The database restore is destructive and is followed by deletion/replacement of the destination vault at line 100. A failed restore, vault extraction, or migration can therefore restart application services against partial or mismatched authoritative state.

Repair: validate both archives before mutation, capture a recovery snapshot, use an atomic database restore where supported, stage the vault before replacement, and restart only after both state components and migrations validate. Test failures at each boundary on disposable infrastructure. This finding is from control-flow inspection; no import was executed.

### P2 — Reject malformed auto-credit booleans and invalid bot context

`packages/factory/business_profiles.py:134` uses `bool(value)` on untyped JSON. A direct probe confirmed `{"allow_flexible_auto_credit": "false"}` becomes `True`. The API accepts business profiles as `dict[str, Any]`, so strict boolean validation is not supplied by its request schema.

Also, `_bot_business_context` in `apps/api/v1/storefront.py:585` returns unrestricted legacy defaults when a signed bot ID resolves to a missing/deleted bot. Distinguish a session with no bot context from a broken existing bot context.

Repair: require actual booleans, reject malformed profiles, and reject invalid signed bot contexts. Cover wrong types, deleted bots, and genuine legacy behavior.

### P2 — Finish the Mini App payment integration

The customer JavaScript has no calls to `/wallet/payment-methods` or `/wallet/flexible-deposits`; its funding flow uses the older provider endpoints. The newer manual/on-chain method and open-amount deposit APIs therefore lack a customer flow in this client.

Repair: add method selection, method-specific instructions, flexible-deposit creation/status, and explicit asset/network/credited-value display. Validate the wizard-to-customer journey with browser tests. Backend API tests alone do not establish this integration.

### P2 — Restore canonical verification and cover new concurrency boundaries

`make verify` fails at Ruff with 197 findings in the local environment (Ruff 0.16.7); 167 are reported automatically fixable. The local virtual environment also lacks `pip`, which the final fast-gate step invokes. Ruff and Aiogram are present here, superseding the earlier missing-dependency handoff for this machine.

The eight PostgreSQL tests cover the older ledger/checkout/claim/RBAC boundaries. Add real-PostgreSQL races for flexible-deposit settlement, asset-wallet creation/credit, hold reserve/capture/release versus debit, and relevant new billing convergence paths.

Repair the lint/environment gate, run `make verify` against an isolated PostgreSQL test database, and then qualify a clean committed release through `make release-gate` plus staging, restore, and enabled-adapter evidence. No PostgreSQL test URL was configured during this review.

### P3 — Reconcile handoff and release identity

Before this review, CURRENT_STATE declared Phase 13 complete but ended by recommending starting Phase 13; AGENT_MAP's header still named Phase 12. The handoff checker accepted that contradiction because it only checks selected markers. These stale headings/next-step instructions are corrected by this review; broader historical claims remain explicitly historical.

The review began with 159 modified/untracked paths and HEAD `af8fb68` (Phase 8). The Phase 9–13 update is not represented by a new committed release identity in this checkout. Preserve the imported work, repair and verify it, then create coherent milestone commits. Do not use the existing HEAD as evidence for this working tree.

## Verification performed

- `PATH="$PWD/.venv/bin:$PATH" make verify`: failed at Ruff, 197 findings. Later canonical steps did not run.
- Full non-PostgreSQL suite outside the sandbox: **371 passed, 8 deselected in 20.67s**, including the installed Aiogram modules. The sandboxed attempt stalled in the aiosqlite fixture; the focused diagnostic timed out. The successful rerun supersedes that environment-limited attempt.
- Direct probes reproduced malformed-boolean enablement and the credited-deposit reconciliation short-circuit.
- Python compileall and Admin/Mini App/Setup JavaScript syntax: passed.
- Handoff consistency and tracked diff whitespace checks: passed, subject to the handoff checker's limited scope.
- Alembic reports one head: `e1f2a3b4c5d6`. No database upgrade/drift check was run in this review.
- `.venv/bin/python -m pip check`: blocked because this virtual environment has no pip module.
- PostgreSQL, Docker release, staging, real provider transactions, and restore drills: not run. No production-readiness claim.

## Next update acceptance order

1. Close the payment-policy bypass, post-credit reversal gap, failed-import restart behavior, and malformed profile handling, with regression tests.
2. Connect the Mini App to the new payment/deposit APIs and verify a full customer journey.
3. Make the complete canonical gate green, including new PostgreSQL concurrency coverage.
4. Commit the reviewed milestone, build one immutable release revision, and collect staging/recovery/adapter evidence before launch.

Defer additional feature expansion until these acceptance criteria are satisfied. The documented multi-item asynchronous fulfillment limitation remains a separate scope decision: restrict launch to supported orders or implement per-item upstream correlation before offering that workflow.
