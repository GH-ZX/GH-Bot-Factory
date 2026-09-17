# ADR-043: Integration Marketplace, Tenant Entitlements, and Safe Credential Boundaries

- **Status:** Accepted
- **Date:** 2026-09-18

## Context

Phase 14.4 establishes the Integration and API Marketplace for GH-Bot-Factory.

Sovereign laws and constraints:
1. **Provider Platform Law (Law 14):** Supplier and payment business logic must remain vendor-neutral. Domain logic consumes canonical categories and capabilities rather than vendor strings. Tenant provider credentials may enter only through write-only `SecretStorage` boundaries; SQL and read APIs must never expose plaintext secret values or vault references.
2. **Commercial Entitlement Boundary (Laws 12, 15):** In a commercial SaaS and reseller deployment, third-party integrations (e.g. 5sim number activation, Binance Pay, custom OpenAPI adapters) carry platform setup and monthly licensing fees. Tenants may browse the full catalog, but they may configure and execute integrations only if their tenant holds an active `TenantIntegrationEntitlement`.
3. **Fail-Closed Authorization:** If a tenant attempts to configure an unentitled integration or bypass commercial tiers, the API must fail closed with `403 Forbidden`.

## Decision

1. **Database Schema & Models (`packages/marketplace/models.py`):**
   - `IntegrationLifecycle`: Enum (`DRAFT`, `SANDBOX_REVIEW`, `ACTIVE`, `DEPRECATED`, `RETIRED`).
   - `IntegrationOfferingModel`: Persistent platform catalog table `integration_offerings`:
     - `key`: Unique indexed string identifier.
     - `name`: Display title.
     - `category`: Canonical category (`NUMBER`, `ACCOUNT`, `GIFT`, `DIGITAL_PRODUCT`, `SERVICE`, `PAYMENT`, `OTHER`).
     - `adapter_key`: Provider adapter or payment engine key.
     - `description`: Plain-English capabilities.
     - `lifecycle`: Lifecycle state.
     - `setup_fee`, `monthly_fee`, `currency`: Platform pricing.
     - `required_credentials`, `supported_templates`, `features`: JSON metadata.
     - `docs_url`: Optional documentation reference.
   - `TenantIntegrationEntitlement`: Tenant entitlement table `tenant_integration_entitlements`:
     - `tenant_id`: Foreign key to `tenants.id` (CASCADE).
     - `integration_key`: Foreign key to `integration_offerings.key` (CASCADE).
     - `is_enabled`: Boolean.
     - `granted_by`: `PLAN`, `QUOTE`, or `OPERATOR`.
     - `granted_at`: Timestamp.
     - Unique constraint on `(tenant_id, integration_key)`.

2. **Database Migration (`d4e5f6a8b9c1`):**
   - Adds tables `integration_offerings` and `tenant_integration_entitlements`.
   - Seeds baseline offerings (`numbers-sms`, `crypto-payments`, `binance-pay`, `gift-cards-api`, `accounts-api`, `custom-http-api`).

3. **Integration Service (`packages/marketplace/integrations_service.py`):**
   - `list_marketplace_integrations`: Returns active catalog annotated with tenant entitlement status (`CONFIGURED`, `ENTITLED`, `LOCKED`).
   - `grant_tenant_entitlement`: Grants or updates entitlement with `PlatformAuditLog`.
   - `revoke_tenant_entitlement`: Disables entitlement and audits.

4. **Platform Control Plane API (`apps/api/v1/platform_sales.py`):**
   - `GET /api/v1/platform/integrations`: Platform catalog and subscriber counts.
   - `POST /api/v1/platform/tenants/{tenant_id}/integrations/{key}/grant`: Operator grants entitlement.
   - `DELETE /api/v1/platform/tenants/{tenant_id}/integrations/{key}`: Operator revokes entitlement.

5. **Tenant Admin Integration Discovery & Credential Entry (`apps/api/v1/admin_integrations.py`):**
   - `GET /api/v1/admin/integrations`: Tenant browses available integrations marketplace.
   - `POST /api/v1/admin/integrations/{key}/configure`: Write-only credential entry into `SecretStorage` for entitled integrations. Fails closed (403) if tenant is not entitled.

6. **Admin Dashboard UI (`apps/admin/static/`):**
   - Integrations Marketplace modal accessible from the Providers tab.
   - Visual badges: `✓ Active & Configured`, `🔓 Entitled (Setup required)`, `🔒 Locked (Add-on required)`.
   - One-click credential entry dialog for entitled integrations.

## Consequences

- Clear commercial and operational separation between platform API offerings and tenant configurations.
- Complete secret safety: provider tokens enter write-only into `SecretStorage`.
- Entitlements can be granted automatically during onboarding or manually by platform operators.
