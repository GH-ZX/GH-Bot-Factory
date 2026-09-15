# Production Readiness Architecture

Phase 7.9 separates four operational truths:

1. **Build truth:** one immutable application image is used by migrations, API, worker, and bot runtime.
2. **Runtime truth:** liveness means a process is alive; readiness means required dependencies are usable; worker/runtime heartbeats prove forward progress.
3. **Data truth:** PostgreSQL is authoritative, Redis is recoverable coordination state, and encrypted off-host PostgreSQL backups are the DR artifact.
4. **Release truth:** a release is the tuple of Git SHA, migration head, verification evidence, image identity, and staging result.

Production containers run non-root, read-only, with a writable `/tmp` only. API rate limiting uses Redis so multiple replicas share the same abuse boundary. PostgreSQL concurrency tests remain mandatory for money/idempotency/claiming changes.
