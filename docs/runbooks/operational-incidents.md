# Operational Incident Runbooks

## Migration failure
1. Stop application rollout; do not start mixed application revisions against an uncertain schema.
2. Capture migration output, current Git SHA, `alembic current`, and the database backup identifier.
3. If the migration is transactional and rolled back, fix forward and rerun in staging first.
4. If data was partially transformed, do not improvise SQL. Use the migration-specific recovery plan or restore a verified backup.
5. Run `alembic check`, PostgreSQL verification, and staging smoke before reopening traffic.

## Provider outage
1. Confirm provider health and scope by tenant/provider.
2. Disable the affected provider or mapping through Admin; preserve credentials and evidence.
3. Do not manually replay `UNKNOWN` fulfillment attempts. Use targeted reconciliation and the fail-closed operator workflow.
4. Re-enable only after provider health check and a controlled staging/test transaction.

## Financial incident
1. Identify payment intent, wallet, ledger transaction, reversal, and financial case IDs.
2. Freeze ambiguous customer value rather than crediting/debiting by ad-hoc SQL.
3. Use Financial Resolution actions; all operator changes must remain audited.
4. For a suspected duplicate settlement/refund, verify database idempotency records before any corrective transaction.

## Redis outage
API readiness must fail for production security controls; workers/runtime heartbeats expire. Restore Redis, verify readiness/heartbeats, then allow traffic. Durable business truth remains in PostgreSQL.

## Telegram outage
Do not rotate bot credentials solely because Telegram is unavailable. Keep durable jobs/state, monitor recovery, and avoid duplicate manual sends or fulfillment actions.
