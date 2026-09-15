# ADR-019: Production Readiness and Release Evidence

## Status
Accepted — 2026-09-15

## Decision
Before Phase 8 scaling, GH-Bot-Factory standardizes on one immutable non-root application image, dependency-aware readiness, Redis-backed process heartbeats/rate limits, PostgreSQL backup/restore drills, staging E2E/failure injection, and a machine-generated release evidence artifact tied to Git SHA and migration head.

## Rationale
Bot Factory multiplies tenants, bot processes, provider calls, and operational blast radius. Scaling before reproducible runtime and recovery controls would make incidents difficult to attribute or safely recover.

## Consequences
- Development bind mounts move to an explicit override file.
- Production services may fail closed when Redis security controls are unavailable.
- PostgreSQL remains the only durable business source of truth.
- A green unit suite alone is insufficient release evidence.
