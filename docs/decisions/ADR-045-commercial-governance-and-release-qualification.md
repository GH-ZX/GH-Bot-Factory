# ADR-045: Commercial Governance, Operational Metrics, and Phase 14 Release Qualification

- **Status:** Accepted
- **Date:** 2026-09-18

## Context

Phase 14 has introduced the complete customer lifecycle for GH-Bot-Factory:
- **14.0:** Server-authoritative template guidance and 4-tier actor boundaries (ADR-039).
- **14.1:** Public configurator on `botfac.gh-store.me/build/`, server-authoritative quote engine, and durable inquiries (ADR-040).
- **14.2:** Platform sales console, in-memory operator credential gate, and immutable commercial quotes (ADR-041).
- **14.3:** Customer onboarding service, tenant `Role.OWNER` assignment, and setup checklist (ADR-042).
- **14.4:** Integration marketplace, tenant entitlements, and write-only `SecretStorage` ingress (ADR-043).
- **14.5:** Single-tenant sanitized export bundles, license tracking, and safe runtime deactivation (ADR-044).

Phase 14.6 completes this milestone with operational commercial governance and canonical release qualification:
1. **Multi-Tenancy Isolation (Law 2):** Platform commercial metrics (inquiry conversion, quote volume, pipeline value, integration adoption, self-hosted licenses) belong strictly to the Platform Operator. They must never be mixed with or leaked into tenant shopper analytics (orders, cart conversions, wallet liability).
2. **Release Evidence Gate (Law 11):** Production readiness requires canonical verification across real PostgreSQL concurrency, Alembic upgrade/drift checks, secret leakage scans, and immutable Docker image validation.

## Decision

1. **Commercial Governance Service (`packages/marketplace/governance.py`):**
   - Implements `CommercialGovernanceService.collect_metrics(session)`:
     - `total_inquiries`: Total prospects captured.
     - `inquiries_by_status`: Breakdown (`NEW`, `CONTACTED`, `QUOTED`, `CONVERTED`, `ARCHIVED`).
     - `inquiry_conversion_rate`: Percentage of inquiries converted to onboarded tenants.
     - `total_quotes`: Total proposals generated.
     - `quotes_by_status`: Breakdown (`DRAFT`, `SENT`, `ACCEPTED`, `REJECTED`, `EXPIRED`, `SUPERSEDED`).
     - `total_pipeline_value`: Total USD amount in active proposals.
     - `total_accepted_revenue`: Total USD amount in accepted agreements (setup + monthly).
     - `total_handoffs`: Count of standalone deployments (`HANDED_OFF`, `EXPORTED`, `PREPARING`).
     - `top_integrations`: Integration offerings sorted by tenant entitlement adoption.
   - Strictly platform-level; requires `require_platform_operator`.

2. **Platform Governance Endpoint & CLI:**
   - Endpoint: `GET /api/v1/platform/sales/governance/metrics` protected by `X-GHBF-Platform-Token`.
   - CLI: `platformctl sales-metrics` for terminal inspection.

3. **Admin Sales Console KPI Metrics:**
   - Real-time KPI summary at the top of `#view-sales` in Admin (`apps/admin/static/app.js`).
   - Displays inquiries, conversion rate, pipeline value, accepted revenue, and active handoffs.

4. **Canonical Release Qualification:**
   - Test suite `tests/test_phase14_6_release_qualification.py` verifying:
     - Accurate metrics calculation and non-contamination with tenant data.
     - Strict isolation between platform and tenant boundaries.
     - Zero secret leakage in export artifacts or API payloads.
     - State machine integrity for inquiries, quotes, and handoffs.

## Consequences

- The Factory Owner has continuous visibility into lead conversion, commercial pipeline, and software licensing.
- Complete separation between platform commercial revenue and tenant retail revenue is maintained.
- All Phase 14 capabilities are validated against the canonical PostgreSQL verification gate and ready for production operations.
