# ADR-044: Dedicated Deployment, Source License Handoff, and Runtime Deactivation Safety

- **Status:** Accepted
- **Date:** 2026-09-18

## Context

Phase 14.5 implements the technical and commercial handoff for customers purchasing **Dedicated Single-Tenant VPS Deployments** (Tier 2) or **Source Code Buyout Licenses** (Tier 3).

Key architectural and operational constraints:
1. **Multi-Tenancy & Data Isolation (Law 2):** A customer purchasing a dedicated installation must never receive an export of the multi-tenant platform database containing other tenants' records, platform audit logs, or platform-level credentials. The export bundle must be strictly single-tenant and sanitized.
2. **Telegram Polling Conflict Invariant (Law 8):** Telegram bot tokens cannot be polled concurrently by two active bot runtimes. If the Factory Owner's managed cluster continues polling after the customer launches their standalone VPS, Telegram will reject polling with HTTP 409 Conflict, causing random message drops, duplicate handling, and fulfillment failures. The managed bot must be deactivated before the customer starts their private server.
3. **Secret Safety (Law 3):** Credentials belonging to the customer's tenant must be extracted safely from `SecretStorage` into a destination-owned credential manifest so the customer can populate their own vault.
4. **Zero-Backdoor Principle:** The handoff terminates the Factory Owner's technical control. The customer operates an autonomous server. Continuing support or maintenance requires an explicit contractual SLA and customer-provided access, not hidden backdoors or remote kill switches.

## Decision

1. **Deployment Handoff Domain Records (`packages/marketplace/models.py`):**
   - `LicenseType`: `MANAGED`, `DEDICATED_DEPLOYMENT`, `SOURCE_LICENSE`.
   - `HandoffStatus`: `PREPARING`, `READY_FOR_EXPORT`, `EXPORTED`, `HANDED_OFF`, `CANCELLED`.
   - `DeploymentHandoff`:
     - `id`: UUID primary key
     - `tenant_id`: UUID, ForeignKey("tenants.id", ondelete="CASCADE"), index
     - `quote_id`: UUID, ForeignKey("commercial_quotes.id", ondelete="SET NULL"), nullable=True, index
     - `license_type`: `LicenseType`
     - `license_key`: String(80), unique, indexed (e.g. `LIC-GHBF-2026-XXXX`)
     - `licensed_to`: String(120)
     - `licensed_domain`: String(120) | None
     - `version_tag`: String(40) (e.g. `v0.1.0-phase14.5`)
     - `status`: `HandoffStatus`
     - `support_plan`: String(60) | None
     - `runtime_deactivated`: Boolean, default=False
     - `runtime_deactivated_at`: DateTime | None
     - `export_checksum`: String(64) | None (SHA-256)
     - `export_artifact_path`: String(255) | None
     - `handoff_notes`: Text | None
     - `handed_off_at`: DateTime | None
   - Database migration `e5f6a8b9c1d2_add_deployment_handoffs.py`.

2. **Single-Tenant Sanitized Handoff Service (`packages/marketplace/handoff_service.py`):**
   - `create_deployment_handoff(...)`: Issues a cryptographically unique license key (`LIC-GHBF-YYYY-XXXX`) and establishes the handoff record.
   - `generate_single_tenant_export_bundle(...)`:
     - Queries strictly tenant-scoped data: `Tenant`, `User` and `Membership(role=Role.OWNER)`, `Bot` configuration, `Product`, `Category`, `ProductVariant`, `PaymentMethodConfig`, and `Provider`.
     - Extracts the customer's provider and bot credentials from `SecretStorage` for their tenant only.
     - Generates a standalone `docker-compose.standalone.yml` and `.env.standalone.example`.
     - Produces a verifiable JSON manifest with SHA-256 checksum and saves to `artifacts/handoffs/`.
   - `deactivate_managed_runtime(...)`:
     - Sets `bot.is_enabled = False` and increments `bot.runtime_revision` on the managed cluster, immediately terminating polling on the factory host.
     - Sets `handoff.runtime_deactivated = True` and `handoff.status = HandoffStatus.HANDED_OFF`.
     - Emits `PlatformAuditLog` (`action="tenant.managed_runtime_deactivated"`).

3. **Platform Sales API (`apps/api/v1/platform_sales.py`):**
   - Protected by `require_platform_operator`.
   - `GET /api/v1/platform/sales/handoffs`: List deployment handoffs with status and license keys.
   - `POST /api/v1/platform/sales/tenants/{tenant_id}/handoffs`: Create a handoff record from an accepted quote or direct sale.
   - `POST /api/v1/platform/sales/handoffs/{handoff_id}/generate-bundle`: Packages the single-tenant bundle and calculates checksum.
   - `POST /api/v1/platform/sales/handoffs/{handoff_id}/deactivate-managed`: Deactivates managed polling and marks handoff complete.

4. **Platform CLI Subcommands (`scripts/platformctl.py`):**
   - `platformctl handoffs`: Lists all active handoffs.
   - `platformctl handoff-create --tenant <id> --license-type <type> --licensed-to <name>`: Creates a new handoff record.
   - `platformctl handoff-bundle --id <id>`: Generates the export bundle.
   - `platformctl handoff-deactivate --id <id>`: Shuts down the managed bot runtime.

5. **Admin Dashboard UI Integration (`apps/admin/static/`):**
   - Added **Deployment Handoffs** tab in the Sales & Leads console.
   - Real-time license status cards showing license key, customer domain, runtime status, and bundle generation controls.
   - Safe deactivation confirmation to prevent accidental customer bot shutdown.

## Consequences

- Customers purchasing dedicated self-hosted setups receive clean, isolated packages with zero leakage of platform or peer-tenant data.
- Deactivating managed polling before customer VPS startup eliminates Telegram HTTP 409 conflict and ensures smooth cutover.
- Formal digital licensing boundaries give both the Factory Owner and customer clear legal and technical ownership.
