# Observability and Incident Response

## Signals
- API: `/health/live` proves process liveness; `/health/ready` proves PostgreSQL + Redis readiness; `/metrics` emits Prometheus text.
- Worker and bot-runtime publish per-container Redis heartbeats; Docker healthchecks fail if the heartbeat expires.
- Logs are JSON by default and carry `service` and `request_id`. Secret-like values and Telegram-token-shaped strings are redacted defensively.

## Correlation
Preserve `X-Request-ID` through the reverse proxy. For an incident, correlate request ID with order ID, payment intent ID, fulfillment job ID, provider transaction ID, and financial case ID. Do not paste credentials into tickets or chat.

## Initial response
1. Determine scope: tenant, service, provider, currency, and time window.
2. Check readiness/heartbeats and 5xx/latency metrics.
3. Freeze high-risk financial actions when evidence is ambiguous; use Financial Resolution rather than ad-hoc SQL.
4. For provider outages, disable routing/configuration through Admin instead of changing DB rows manually.
5. Preserve logs, audit records, provider evidence, and release SHA.

## Credential compromise
Rotate the affected secret in the external secret store/environment, update the secret reference only if the reference name changes, restart affected services, revoke/rotate upstream credentials, and review audit/provider/payment activity for the exposure window. For JWT compromise, rotate `JWT_SECRET_KEY`; all existing access tokens become invalid.
