# ADR-041: Platform Sales Console, Commercial Quotes, and Owner Lead Management

- **Status:** Accepted
- **Date:** 2026-09-18

## Context

Phase 14.1 introduced the public configurator (`/build/`) and durable `CustomerInquiry` lead capture. Phase 14.2 introduces the Factory Owner's sales console to review incoming requests, communicate with prospects, and generate versioned, immutable commercial quotes.

Key requirements and constraints:
1. **Platform Control Plane Boundary (Law 12):** Platform-operator authority is installation-level and must remain strictly separate from tenant RBAC. Inquiries arrive before a tenant exists, and quotes represent platform-level commercial agreements (platform fees, hosting, and API add-ons), not tenant retail sales.
2. **Credential Safety (Law 3):** To prevent leaking the installation-level platform token into browser persistence (`localStorage` or `sessionStorage`), the Admin Sales Console must keep the platform token in session memory only.
3. **Immutable Quotes (Law 6):** Once a commercial quote is accepted, it must be locked immutably against further edits. Accepted quotes stamp the accepted revision, line items, and currency, emitting `PlatformAuditLog` for non-repudiable audit evidence.
4. **Dual Access Modalities:** The Factory Owner must be able to manage sales both through the browser Admin dashboard (`apps/admin/`) and through the local-first CLI (`scripts/platformctl.py`).

## Decision

1. **Commercial Quote Domain Records (`packages/marketplace/models.py`):**
   - Define `CommercialQuote` and `CommercialQuoteLine`:
     - `CommercialQuote`: Stores `id`, `inquiry_id`, `quote_number` (e.g. `Q-2026-0001`), `version`, `customer_name`, `customer_contact`, `status` (`DRAFT`, `SENT`, `ACCEPTED`, `REJECTED`, `EXPIRED`, `SUPERSEDED`), `valid_until`, `currency`, `total_one_time`, `total_monthly`, `terms`, `notes`, `accepted_at`, `created_at`, `updated_at`.
     - `CommercialQuoteLine`: Stores `id`, `quote_id`, `name`, `category`, `item_type` (`one_time`, `recurring`), `amount`, `description`.
   - Add database migration `b2c3d4e5f6a8_add_commercial_quotes.py`.

2. **Platform Sales API (`apps/api/v1/platform_sales.py`):**
   - Protected by `require_platform_operator` (`X-GHBF-Platform-Token`).
   - `GET /api/v1/platform/sales/inquiries`: List customer inquiries with pagination, status filtering, and search.
   - `GET /api/v1/platform/sales/inquiries/{id}`: Detailed inquiry view with full configuration and quote history.
   - `PATCH /api/v1/platform/sales/inquiries/{id}/status`: Update lead status (`NEW`, `CONTACTED`, `QUOTED`, `CONVERTED`, `ARCHIVED`) and emit `PlatformAuditLog`.
   - `POST /api/v1/platform/sales/inquiries/{id}/quotes`: Generate a new versioned `CommercialQuote` from the inquiry or custom adjustments.
   - `GET /api/v1/platform/sales/quotes`: List commercial quotes.
   - `GET /api/v1/platform/sales/quotes/{id}`: Full quote with itemized line items.
   - `POST /api/v1/platform/sales/quotes/{id}/accept`: Immutably freeze quote status to `ACCEPTED`, supersede prior draft revisions, update inquiry status to `CONVERTED`, and record `PlatformAuditLog`.

3. **In-Memory Operator Token Gate in Admin (`apps/admin/static/`):**
   - Add a dedicated **Sales & Leads** nav item (`view-sales`) in Admin.
   - When accessed, if `state.platformToken` is unset in JS memory, prompt the operator to enter their platform token.
   - Token is held strictly in JavaScript heap memory and never written to `localStorage` or `sessionStorage`.
   - Requests to `/api/v1/platform/sales/...` include `X-GHBF-Platform-Token`.

4. **CLI Support (`scripts/platformctl.py`):**
   - Add `inquiries` and `quotes` subcommands to `platformctl` for headless server management via SSH.

## Consequences

- The Factory Owner can convert incoming inquiries into accepted commercial contracts without manual database edits.
- Client browsers cannot tamper with pricing; quotes snapshot server-calculated baselines and can only be altered by an authenticated platform operator.
- Separation of platform authority and tenant RBAC is strictly preserved.
