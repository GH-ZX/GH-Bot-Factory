# Production Deploy and Rollback

## Preconditions
- `make release-gate` passed against PostgreSQL 17.
- Latest staging E2E passed; failure-injection evidence is current for a release that changes durability, money, Redis, PostgreSQL, workers, or runtime lifecycle.
- A recent encrypted PostgreSQL backup exists and its restore drill is within policy.
- Image is built from a reviewed commit and tagged with the immutable Git SHA.

## Deploy
1. Build/publish `gh-bot-factory:<git-sha>`; never deploy `latest` as the audit identity.
2. Set `GHBF_IMAGE_TAG=<git-sha>` and production `.env` outside Git.
3. `docker compose pull` when using a registry, then `docker compose run --rm migrate`.
4. Start API, worker, and bot-runtime with `docker compose up -d --no-deps api worker bot-runtime`.
5. Verify `/health/live`, `/health/ready`, service health, Prometheus scrape, and one read-only Admin/Storefront smoke flow.
6. Record the SHA, migration head, operator, and release evidence path.

## Rollback
Application rollback is safe only when the previous image understands the current schema. If a migration is backward-incompatible, use the migration-specific rollback plan or restore procedure rather than blindly starting an old image. Never downgrade a financial schema without a reviewed data-preservation plan.
