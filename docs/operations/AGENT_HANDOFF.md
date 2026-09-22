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
