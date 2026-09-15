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
