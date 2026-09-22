# Coding Agent Handoff Protocol

Use this protocol whenever an agent starts, pauses, or completes work on GH-Bot-Factory.

## Resume Checklist

1. Read `docs/operations/CURRENT_STATE.md`.
2. Read the sovereign laws at the top of `AGENT_MAP.md`.
3. Read only the ADRs relevant to the subsystem being changed.
4. Inspect the latest migration head before creating schema changes.
5. Inspect existing services/state machines before adding new abstractions.
6. Check the latest runnable and canonical verification status; never assume a prior green suite still applies after edits.

## Milestone Completion Documentation Gate

A milestone is not complete until all applicable artifacts are updated:

- `docs/operations/CURRENT_STATE.md`: current phase, migration head, verification baseline, blockers, and next phase.
- `docs/operations/CHANGELOG_AGENT.md`: concise technical handoff entry with affected boundaries and migration IDs.
- `docs/roadmap.md`: phase status and delivered scope.
- `AGENT_MAP.md`: architecture map/laws/milestone ledger when the change affects architecture, security, packages, tables, routes, or operating rules.
- `docs/prompts/prompt_history.md`: the user prompt verbatim plus implementation/verification summary.
- `docs/decisions/ADR-*.md`: required for architectural, security, accounting, tenancy, or irreversible persistence decisions.
- `.agents/skills/gh-bot-factory-core/SKILL.md`: update when the agent execution protocol or non-negotiable rules change.

## Verification Handoff

Record results exactly. Distinguish:

- **Canonical gate:** full declared environment (`pytest`, Ruff, production-relevant integration checks).
- **Runnable/local gate:** what the current execution environment could actually run.
- **Blocked gate:** name the missing dependency or environment capability explicitly.

Never write “all tests pass” when modules were skipped or dependencies were unavailable.

## Change Design Principles

- Prefer domain services over route-local business logic.
- Keep browser/Mini App clients presentation-only and untrusted.
- Add indexes deliberately for new high-frequency query shapes.
- Do not add aggregate tables/caches until query measurements justify them; derived analytics must remain reproducible from authoritative records.
- Preserve evidence: financial/provider/audit history should be append-only or resolution-linked rather than deleted to make dashboards look clean.

## Canonical Production-Readiness Gate

Use the repository commands instead of ad-hoc verification:

- `make verify-fast` for rapid local feedback.
- `POSTGRES_TEST_DATABASE_URL=postgresql+asyncpg://... make verify-postgres` for production-engine verification.
- `POSTGRES_TEST_DATABASE_URL=postgresql+asyncpg://... make verify` before a milestone commit/push.

A database-sensitive milestone is blocked until the PostgreSQL gate runs successfully. Never substitute SQLite for concurrency evidence. `alembic check` is mandatory after any model or migration change.

## Web Factory Live Completion — 2026-09-22

All five current web-factory steps are complete and checked in docs/plans/web-factory-completion.md. Source `240a106` is pushed and deployed. `/setup/` is redesigned and now shows the initialized state; `ahmedghx` is configured with the supplied password (never recorded here). The factory has zero owner bots. Browser login at https://factory.gh-store.me/admin/ succeeds; Settings & operations → Sales & leads opens using the same password session without another token. Customer tenant roles do not grant factory/platform access.

Shared theme: apps/shared/static/theme.css. Builder, Admin/sign-in and setup share the brand primitive and derived colors. GH Store's logo mark is copied unchanged from sibling gh-store-dev. Customer bot branding remains independent. Customer brief → one-time quote acceptance → tenant configuration is covered through API/database integration tests; accepted scope preserves template/color/language and requested features without granting unreviewed entitlements.

Final isolated-source canonical verification: 486 fast + 20 PostgreSQL tests, migration/no drift, Ruff, secret/handoff/JavaScript/compilation/dependency checks. Four browser suites passed. Live browser checks passed for actual owner password login, factory checklist, sales access without platform-token entry, logout, mobile layout and locked setup; desktop/mobile screenshots inspected. An initial browser attempt could not click Sales inside a collapsed navigation section; opening Settings & operations fixed the walkthrough without application changes.

Deployment: clean-archive image gh-bot-factory:240a106, revision label 240a106, immutable image identity sha256:8be78618916743a97793107f83ed0e6282e29ea4144b44badbf170e419d1be9f. API/worker/bot-runtime use this image; all five services healthy. Local alias promoted; previous image retained as before-web-240a106. Migration 0ce5d2c98e7f → a71c9e23b840 applied after restricted backup backups/pre-web-factory/ghbf-20260921T235259Z.dump (checksum alongside). Owner initialized locally through the validated setup service in one transaction, reusing the explicitly requested orphan account; no Telegram Bot record created. No database/Redis/tunnel/other-host-container configuration was changed.

The initial Compose build reported success but its tag was unavailable at validation. Its fallback build was stopped before migration/rollout. A direct build from the verified archive was validated with its revision and dependency check, then deployed with building disabled. No unreviewed fallback image was deployed.

Evidence: /tmp/ghbf-web-canonical.log, /tmp/ghbf-web-build-direct.log, /tmp/ghbf-web-migrate.log, /tmp/ghbf-web-live-dashboard.png, /tmp/ghbf-web-live-setup.png. Development-installation acceptance only: real-customer Supabase/VPS testing, shopper/bilingual polish, automated Docker/migration/report packaging and production release qualification remain deferred/incomplete.


## 2026-09-22 — Completion batch deployed; verification deferred

- Built and deployed `gh-bot-factory:work-20260922-completion` from uncommitted working source.
- Applied PostgreSQL migration `b82da430c951 → c93eb541da62` successfully after the restricted
  backup `backups/product-20260922/before-completion.dump`. No restore drill was performed.
- Recreated only project API, worker and bot-runtime containers. Docker reports all five
  project services healthy; PostgreSQL/Redis containers and Cloudflare tunnels were unchanged.
- Delivery center, Store settings, Releases & fixes and the expanded MiniApp are in this image.
  Sample design preview: https://factory.gh-store.me/miniapp/?preview=1 (sample data only).
- Implementation and deployment boxes are checked in `docs/plans/factory-completion-next.md`.
  No functional tests, browser runs, verification gates, real-tenant trials, commits or pushes.
  Startup health is deployment evidence only; this source is not production-qualified.
- Build log: `/tmp/ghbf-completion-build.log`; Compose override: `/tmp/ghbf-completion-runtime.yml`.
  Use the override for this working image; do not assume the base Compose image tag includes it.
- Next: await the owner's authorization for the batched verification/fix pass, then commit/push
  after required gates pass. Real-customer delivery remains separately deferred.


## 2026-09-22 — Hardening batch deployed, functional verification deferred

Image `gh-bot-factory:work-20260922-hardening` built and deployed from uncommitted working
source. Only API, worker and bot-runtime were recreated. Docker reports all five project
services healthy. No migration was required or executed; schema remains `c93eb541da62`.
PostgreSQL/Redis containers, other host containers, ports and Cloudflare tunnels were unchanged.
Build log: `/tmp/ghbf-hardening-build.log`; working-image Compose override:
`/tmp/ghbf-hardening-runtime.yml`. Retain/use this override for the deployed working image.

Visible additions: Admin → Needs attention; Store settings → Pause new purchases and
Installation & backup evidence; Account → Sign out all account sessions; richer Delivery
center cards; MiniApp recovery panel when an unresolved money request exists. Generated
packages include the new guards/receipts/backup helper. Previously exported ZIPs must be regenerated.

All ten targeted coding upgrades and this deployment are checked in `docs/plans/factory-hardening.md`.
No automated tests, lint/syntax gates, browser checks, real-customer operations, commit or push.
Container startup is not feature acceptance or production qualification. The next authorized
verification batch must cover the new financial retry/locking behavior, tenant/RBAC isolation,
package lifecycle/import/backup/restore, UI/RTL and security boundaries before release claims.


## 2026-09-22 — Customer product verification and MiniApp entry repair

Owner authorized testing, fixes, commit and push after successful gates. Earlier test deferrals
are historical. Real-customer trials remain deferred.

- MiniApp entry now redirects an unconfigured bare URL to the explicitly labelled preview.
  Bot-bearing links preserve signed-Telegram authentication and never silently become sample sessions.
- Render-blocking neutral skeleton hides the unthemed header, content, overlays and navigation;
  the selected theme is applied before revealing content. Errors use the current light identity
  and offer a preview link. Browser checks delayed the theme response and inspected screenshots.
- Fixed a PostgreSQL stale-identity-map defect: checkout refreshes the paused-sales policy under
  its shared row lock. A two-session regression proves a previously loaded tenant cannot bypass pause.
- Corrected restore rejection wording while preserving the empty/uninitialized destination guard.
- Verification now syntax-checks every Admin/MiniApp/shared JS module. Browser fixtures serve the
  real static dependency graph, including theme JSON and new modules.
- Added tests for scope/RBAC, version conflicts, bounded/revoked diagnostic grants, update evidence,
  alert acknowledgement, session revocation, secret-safe validation, pause/replay, package integrity,
  lifecycle rejection, fresh PostgreSQL SQL initialization and tenant restore, and browser money recovery.
- **Canonical `make verify` passed: 500 fast + 22 PostgreSQL**, no schema drift, Ruff, compilation,
  handoff/secret checks, JavaScript syntax, whitespace and dependency consistency. Two existing
  FastAPI deprecation warnings remain; they do not fail tests.
- **Seven browser suites passed:** Admin/login/MiniApp payments, operations, setup wizard, web setup,
  MiniApp entry/themes/RTL/skeleton, lost-response recovery, and hardening workspaces.
- Strict installed third-party dependency audit passed after updating the isolated development
  environment's pip to 26.2.1. `scripts/audit_dependencies.py` audits exact installed versions;
  it excludes only this first-party editable app, whose source is covered by the repository gates.
  pip-audit cannot query this private package from PyPI; no third-party finding is ignored.
- Source schema remains `c93eb541da62`. Fresh package PostgreSQL bootstrap/import passed on an
  isolated local database, not a real Supabase account or customer VPS. Remote provider acceptance,
  actual Telegram customer trial and staging/failure-injection qualification remain separate.
- Logs: `/tmp/ghbf-canonical-final.log`, `/tmp/ghbf-dependency-audit.log`, browser logs under
  `/tmp/ghbf-*-final-browser.log`, `/tmp/ghbf-miniapp-browser.log`. Release artifact build pending.
