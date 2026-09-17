# ADR-042: Customer Tenant Onboarding, Owner Assignment, and Launch Checklist

- **Status:** Accepted
- **Date:** 2026-09-18

## Context

Following Phase 14.1 (public inquiries) and Phase 14.2 (commercial quotes), Phase 14.3 automates the handoff from an accepted quote into an operational tenant workspace.

Sovereign constraints:
1. **Multi-Tenancy & Privilege Separation (Laws 2, 12, 15):** The customer must become the sole `OWNER` of their tenant workspace. They must never receive platform control-plane authority. Conversely, the Factory Owner must not perform routine catalog entry or manage supplier API credentials for the customer.
2. **Self-Service Customer Activation:** The newly created tenant owner must be guided through self-service configuration (branding, catalog upload, payment setup, provider API key entry) to reach launch readiness.
3. **Auditability & Provenance:** The resulting `Tenant` must link directly back to the accepted `CommercialQuote`, recording non-repudiable audit evidence in `PlatformAuditLog`.

## Decision

1. **Quote-to-Tenant Linkage:**
   - Add `tenant_id` foreign key to `CommercialQuote` in `packages/marketplace/models.py`.
   - Migration `c3d4e5f6a8b9_link_quote_to_tenant.py` adds the indexed foreign key column.

2. **Customer Onboarding Service (`packages/marketplace/onboarding.py`):**
   - Implements `onboard_customer_from_quote`:
     - Verifies quote is `ACCEPTED` and not previously onboarded.
     - Idempotently provisions or binds `Tenant` (`name`, `slug`, `is_active=True`).
     - Provisions `User` and `Membership(role=Role.OWNER, is_active=True)`.
     - Assigns subscription plan matching the quote format and template.
     - Provisions initial desired `Bot` record with template configuration and business profile.
     - Generates ephemeral single-use admin sign-in grant via `AdminLoginService`.
     - Records `PlatformAuditLog` (`action="tenant.onboarded_from_quote"`).
     - Returns tenant metadata, admin launch link, and owner credentials.

3. **Platform Route & CLI:**
   - `POST /api/v1/platform/sales/quotes/{quote_id}/onboard`: Protected by `require_platform_operator`.
   - CLI: `platformctl quote-onboard --id <quote_id> --slug <slug> --name <name> --owner <handle>`.

4. **Tenant Onboarding Checklist API (`apps/api/v1/admin_onboarding.py`):**
   - `GET /api/v1/admin/onboarding/checklist` (accessible to tenant staff):
     - Evaluates 5 core launch requirements:
       1. Store Branding (name, tagline, accent)
       2. Catalog & Products (at least one active product)
       3. Payment Methods (at least one enabled payment method)
       4. Supplier Providers (required for API/hybrid templates)
       5. Telegram Bot Identity (valid BotFather token verification)
     - Computes overall completion percentage, next actionable step, and launch readiness.

5. **Admin Dashboard Checklist UI Widget (`apps/admin/static/`):**
   - Displays a responsive progress card at the top of the Overview dashboard.
   - Shows progress bar, completed steps, and direct navigation buttons to configure pending items.
   - Disappears automatically once the store is 100% launch-ready.

## Consequences

- End-to-end automation from lead capture to accepted quote to tenant creation.
- Customers have complete autonomy over their store data without requiring Factory Owner intervention.
- The platform retains an immutable audit trail linking commercial quotes to tenant records.
