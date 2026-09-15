# Security and Abuse Hardening

Production/staging startup is fail-closed unless PostgreSQL, Redis, a >=32-byte JWT secret, HTTPS Mini App URL (when configured), and Redis-backed rate limiting are configured.

Controls include request body limits, sensitive-route rate limiting, HSTS, nosniff/referrer/permissions/frame/CSP headers, disabled API docs in production, trusted proxy configuration, JSON log redaction, repository secret scanning, dependency audit in CI, non-root/read-only containers, and no-new-privileges.

Do not expose `/metrics` publicly at the edge. Bind the application port to localhost/private ingress and explicitly route only intended endpoints. Configure `FORWARDED_ALLOW_IPS` to the exact trusted reverse-proxy address/network, never `*` on an internet-facing host.
